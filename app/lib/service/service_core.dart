/// 前台服务 isolate 内核：WS 长连 + 录音推流 + 断线缓冲补传。
///
/// 录音与 WS 都跑在服务 isolate 里，应用被划掉后仍持续工作；
/// 与 UI 之间用 flutter_background_service 的 invoke/on 通道通信。
library;

import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';
import 'dart:ui' show DartPluginRegistrant;

import 'package:flutter/services.dart' show PlatformException;
import 'package:flutter_background_service/flutter_background_service.dart';
import 'package:record/record.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../net/protocol.dart' as p;

const prefsHostKey = 'server_host'; // host:port
const prefsTokenKey = 'server_token';

/// 服务 isolate 的核心状态（onStart 中创建，随 isolate 存活）。
class ServiceCore {
  ServiceCore._(this._service) {
    _service.on('cmd').listen(_onCmd);
    _connectLoop();
  }

  static ServiceCore? _instance;

  static ServiceCore start(ServiceInstance service) {
    _instance ??= ServiceCore._(service);
    return _instance!;
  }

  final ServiceInstance _service;

  WebSocketChannel? _ws;
  String _hostPort = '';
  String _token = '';
  bool _stopping = false;
  int _backoff = 2;

  // 音频序号与未确认缓冲（断线补传）
  int _seq = 0;
  final Map<int, Uint8List> _unacked = {};

  AudioRecorder? _recorder;
  StreamSubscription<Uint8List>? _audioSub;
  bool _recording = false;
  String _course = '';
  int _chunksSent = 0;

  // ---------- UI 通信 ----------

  void _emit(String type, Map<String, dynamic> payload) {
    _service.invoke('event', {'type': type, ...payload});
  }

  void _setStatus(String status, [String detail = '']) {
    _emit('conn', {'status': status, 'detail': detail});
  }

  Future<void> _updateNotification(String title, String content) async {
    try {
      await _service.setForegroundNotificationInfo(title: title, content: content);
    } on PlatformException {
      // 通知不可用时忽略
    }
  }

  // ---------- 命令处理 ----------

  Future<void> _onCmd(dynamic raw) async {
    final msg = Map<String, dynamic>.from(raw as Map);
    final cmd = msg['cmd'] as String?;
    switch (cmd) {
      case 'settings':
        _hostPort = (msg['hostPort'] as String?) ?? _hostPort;
        _token = (msg['token'] as String?) ?? _token;
        break;
      case 'start_session':
        _course = (msg['course'] as String?) ?? '';
        _seq = 0;
        _unacked.clear();
        _sendJson({'type': p.msgStartSession, 'course': _course});
        break;
      case 'stop_session':
        _sendJson({'type': p.msgStopSession});
        break;
      case 'manual_alert':
        _sendJson({'type': p.msgManualAlert});
        break;
      case 'stop_service':
        _stopping = true;
        await _stopRecording();
        await _ws?.sink.close();
        await _service.stopSelf();
        break;
    }
  }

  // ---------- WS 连接循环 ----------

  Future<void> _connectLoop() async {
    final prefs = await SharedPreferences.getInstance();
    _hostPort = prefs.getString(prefsHostKey) ?? '';
    _token = prefs.getString(prefsTokenKey) ?? '';
    if (_hostPort.isEmpty) {
      _setStatus('error', '未配置服务器地址');
      return;
    }
    while (!_stopping) {
      try {
        _setStatus('connecting');
        final uri = Uri.parse('ws://$_hostPort/ws');
        final channel = WebSocketChannel.connect(uri);
        await channel.ready.timeout(const Duration(seconds: 8));
        _ws = channel;
        _backoff = 2;
        _sendJson({'type': p.msgAuth, 'token': _token});
        _setStatus('connected');
        await _updateNotification('class-helper', '已连接服务器');
        // 重发断线期间积压的音频
        for (final seq in _unacked.keys.toList()..sort()) {
          channel.sink.add(p.packAudio(seq, _unacked[seq]!));
        }
        await channel.stream.listen(_onServerMessage).asFuture<void>();
      } catch (e) {
        _setStatus('disconnected', e.toString());
      } finally {
        _ws = null;
      }
      if (_stopping) break;
      _setStatus('disconnected', '$_backoff 秒后重连');
      await Future.delayed(Duration(seconds: _backoff));
      _backoff = (_backoff * 2).clamp(2, 30);
    }
  }

  void _onServerMessage(dynamic data) {
    if (data is! String) return;
    final Map<String, dynamic> msg;
    try {
      msg = Map<String, dynamic>.from(jsonDecode(data) as Map);
    } on FormatException {
      return;
    }
    final type = msg['type'] as String?;
    if (type == p.msgError) {
      _emit('server_error', {'message': msg['message'] ?? ''});
      return;
    }
    if (type == p.msgAudioAck) {
      final ack = (msg['seq'] as num).toInt();
      _unacked.removeWhere((seq, _) => seq <= ack);
      return;
    }
    if (type == p.msgSessionStarted) {
      _startRecording();
    } else if (type == p.msgSessionStopped) {
      _stopRecording();
    }
    // 其余事件原样转发 UI
    _service.invoke('event', msg);
  }

  // ---------- 录音与推流 ----------

  Future<void> _startRecording() async {
    if (_recording) return;
    final recorder = AudioRecorder();
    try {
      final stream = await recorder.startStream(const RecordConfig(
        encoder: AudioEncoder.pcm16bits,
        sampleRate: p.audioRate,
        numChannels: 1,
      ));
      _recorder = recorder;
      _recording = true;
      _setStatus('recording');
      await _updateNotification('class-helper 录音中', '课程: $_course');
      _audioSub = stream.listen(_onAudioChunk, onError: (Object e) {
        _emit('conn', {'status': 'recording_error', 'detail': e.toString()});
      });
    } catch (e) {
      _emit('conn', {'status': 'recording_error', 'detail': e.toString()});
    }
  }

  Future<void> _stopRecording() async {
    _audioSub?.cancel();
    _audioSub = null;
    await _recorder?.stop();
    await _recorder?.dispose();
    _recorder = null;
    if (_recording) {
      _recording = false;
      _setStatus('connected');
      await _updateNotification('class-helper', '已连接服务器');
    }
  }

  void _onAudioChunk(Uint8List chunk) {
    if (chunk.isEmpty) return;
    final seq = _seq++;
    _unacked[seq] = chunk;
    final ws = _ws;
    if (ws != null) {
      ws.sink.add(p.packAudio(seq, chunk));
      if (++_chunksSent % 20 == 0) {
        _emit('status', {'chunks': _chunksSent});
      }
    }
    // 断线时留在 _unacked，重连后按序补传
  }

  void _sendJson(Map<String, dynamic> msg) {
    _ws?.sink.add(jsonEncode(msg));
  }
}

/// 服务 isolate 入口。
@pragma('vm:entry-point')
Future<void> serviceEntryPoint(ServiceInstance service) async {
  DartPluginRegistrant.ensureInitialized();
  ServiceCore.start(service);
}
