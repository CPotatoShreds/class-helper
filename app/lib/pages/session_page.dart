import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_background_service/flutter_background_service.dart';

import '../main.dart' show showAlertNotification;

/// 课堂页：连接状态、会话控制、转写流、告警卡与速答、手动大按钮。
class SessionPage extends StatefulWidget {
  const SessionPage({super.key, required this.service});

  final FlutterBackgroundService service;

  @override
  State<SessionPage> createState() => _SessionPageState();
}

class _TranscriptLine {
  _TranscriptLine(this.wall, this.text);
  final String wall;
  final String text;
}

class _SessionPageState extends State<SessionPage> {
  final _courseCtrl = TextEditingController();
  String _connStatus = 'connecting';
  String _connDetail = '';
  bool _recording = false;
  bool _sessionActive = false;
  String _course = '';
  final List<_TranscriptLine> _lines = [];
  String? _preparingReason; // 黄色预警
  String? _calledQuestion; // 红色告警：老师的问题
  final StringBuffer _answerBuf = StringBuffer();
  String _answer = '';
  StreamSubscription<dynamic>? _events;
  final ScrollController _scroll = ScrollController();

  @override
  void initState() {
    super.initState();
    _events = widget.service.on('event').listen(_onEvent);
  }

  @override
  void dispose() {
    _events?.cancel();
    _scroll.dispose();
    super.dispose();
  }

  void _onEvent(dynamic raw) {
    if (raw == null || !mounted) return;
    final msg = Map<String, dynamic>.from(raw as Map);
    final type = msg['type'];
    switch (type) {
      case 'conn':
        setState(() {
          _connStatus = msg['status'] ?? '';
          _connDetail = msg['detail'] ?? '';
          if (_connStatus == 'recording') {
            _recording = true;
            _sessionActive = true;
          } else if (_connStatus == 'connected') {
            _recording = false;
          }
        });
        break;
      case 'status':
        // asr_loading / asr_ready / asr_failed
        setState(() => _connDetail = (msg['message'] ?? '') as String);
        break;
      case 'transcript':
        setState(() {
          _lines.add(_TranscriptLine((msg['wall'] ?? '') as String, (msg['text'] ?? '') as String));
          if (_lines.length > 200) _lines.removeAt(0);
        });
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (_scroll.hasClients) _scroll.jumpTo(_scroll.position.maxScrollExtent);
        });
        break;
      case 'alert':
        final level = msg['level'];
        if (level == 'preparing') {
          setState(() => _preparingReason = (msg['reason'] ?? '') as String);
        } else if (level == 'called') {
          setState(() {
            _preparingReason = null;
            _calledQuestion = (msg['question'] ?? '') as String;
            _answer = '';
            _answerBuf.clear();
          });
          showAlertNotification(_calledQuestion ?? '');
        }
        break;
      case 'answer_delta':
        setState(() => _answerBuf.write(msg['text']));
        break;
      case 'answer_done':
        setState(() {
          _answer = _answerBuf.toString();
          _answer = _answer.isEmpty ? (msg['answer'] ?? '') as String : _answer;
        });
        break;
      case 'session_started':
        setState(() {
          _sessionActive = true;
          _recording = true;
          _course = (msg['course'] ?? '') as String;
          _calledQuestion = null;
          _preparingReason = null;
          _answer = '';
        });
        break;
      case 'session_stopped':
        setState(() {
          _sessionActive = false;
          _recording = false;
          _lines.clear();
        });
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            content: Text('课程已结束，总结已归档：${msg['summary_path'] ?? ''}'),
          ));
        }
        break;
      case 'server_error':
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('服务器: ${msg['message']}')),
          );
        }
        break;
    }
  }

  void _send(String cmd, [Map<String, dynamic> extra = const {}]) {
    widget.service.invoke('cmd', {'cmd': cmd, ...extra});
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(_course.isEmpty ? '课堂值守' : '课堂值守 · $_course'),
        backgroundColor: _connBarColor,
      ),
      body: Column(
        children: [
          _buildConnStrip(),
          if (_preparingReason != null) _buildPreparingBanner(),
          if (_calledQuestion != null) _buildCalledCard(),
          if (!_sessionActive) _buildCourseInput(),
          Expanded(child: _buildTranscriptList()),
          if (_sessionActive) _buildManualButton(),
        ],
      ),
    );
  }

  Color get _connBarColor => switch (_connStatus) {
        'recording' => Colors.green,
        'connected' => Colors.teal,
        'connecting' => Colors.orange,
        _ => Colors.red,
      };

  Widget _buildConnStrip() {
    final text = switch (_connStatus) {
      'recording' => '● 录音中',
      'connected' => '已连接（未录音）',
      'connecting' => '连接中...',
      _ => '连接断开：$_connDetail',
    };
    return Material(
      color: _connBarColor.withOpacity(0.12),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        child: Row(
          children: [
            Icon(
              _connStatus == 'recording' ? Icons.mic : Icons.mic_off,
              size: 16,
              color: _connBarColor,
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Text(text,
                  style: TextStyle(fontSize: 12, color: Colors.grey.shade800)),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildPreparingBanner() {
    return Container(
      width: double.infinity,
      color: Colors.amber.shade200,
      padding: const EdgeInsets.all(10),
      child: Row(children: [
        const Icon(Icons.warning_amber_rounded, color: Colors.deepOrange),
        const SizedBox(width: 8),
        Expanded(
          child: Text('老师可能准备点名：$_preparingReason',
              style: const TextStyle(fontWeight: FontWeight.w600)),
        ),
      ]),
    );
  }

  Widget _buildCalledCard() {
    return Container(
      width: double.infinity,
      color: Colors.red.shade100,
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Row(children: [
            Icon(Icons.notification_important, color: Colors.red),
            SizedBox(width: 8),
            Text('老师点名了！', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
          ]),
          if ((_calledQuestion ?? '').isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text('问题：$_calledQuestion',
                  style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
            ),
          const SizedBox(height: 8),
          Text(
            _answer.isEmpty && _answerBuf.isEmpty ? '速答生成中...' : (_answer.isEmpty ? _answerBuf.toString() : _answer),
            style: const TextStyle(fontSize: 18, height: 1.5),
          ),
        ],
      ),
    );
  }

  Widget _buildCourseInput() {
    return Padding(
      padding: const EdgeInsets.all(12),
      child: Row(children: [
        Expanded(
          child: TextField(
            controller: _courseCtrl,
            decoration: const InputDecoration(
              labelText: '课程名',
              hintText: '如：计算机网络',
              border: OutlineInputBorder(),
              isDense: true,
            ),
          ),
        ),
        const SizedBox(width: 10),
        FilledButton(
          onPressed: () {
            final course = _courseCtrl.text.trim();
            if (course.isEmpty) return;
            _send('start_session', {'course': course});
          },
          child: const Text('开始上课'),
        ),
      ]),
    );
  }

  Widget _buildTranscriptList() {
    if (_lines.isEmpty) {
      return const Center(child: Text('开始上课后，老师的讲话会实时出现在这里'));
    }
    return ListView.builder(
      controller: _scroll,
      padding: const EdgeInsets.all(12),
      itemCount: _lines.length,
      itemBuilder: (_, i) => Padding(
        padding: const EdgeInsets.only(bottom: 6),
        child: RichText(
          text: TextSpan(
            style: DefaultTextStyle.of(context).style,
            children: [
              TextSpan(
                text: '${_lines[i].wall}  ',
                style: TextStyle(color: Colors.grey.shade600, fontSize: 12),
              ),
              TextSpan(text: _lines[i].text),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildManualButton() {
    return Padding(
      padding: const EdgeInsets.all(14),
      child: Column(children: [
        SizedBox(
          width: double.infinity,
          height: 64,
          child: FilledButton.icon(
            style: FilledButton.styleFrom(backgroundColor: Colors.red),
            onPressed: () => _send('manual_alert'),
            icon: const Icon(Icons.emergency, size: 28),
            label: const Text('我被点了！', style: TextStyle(fontSize: 20)),
          ),
        ),
        const SizedBox(height: 10),
        SizedBox(
          width: double.infinity,
          child: OutlinedButton(
            onPressed: () => _send('stop_session'),
            child: const Text('下课（结束归档）'),
          ),
        ),
      ]),
    );
  }
}
