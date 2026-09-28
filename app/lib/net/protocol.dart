/// WS 协议常量与音频帧编解码（与服务端 class_helper.protocol 对应）。
library;

import 'dart:typed_data';

const int audioRate = 16000;

// 客户端 -> 服务端
const String msgAuth = 'auth';
const String msgStartSession = 'start_session';
const String msgStopSession = 'stop_session';
const String msgManualAlert = 'manual_alert';
const String msgPing = 'ping';

// 服务端 -> 客户端
const String msgAuthOk = 'auth_ok';
const String msgError = 'error';
const String msgSessionStarted = 'session_started';
const String msgSessionStopped = 'session_stopped';
const String msgTranscript = 'transcript';
const String msgAlert = 'alert';
const String msgAnswerDelta = 'answer_delta';
const String msgAnswerDone = 'answer_done';
const String msgStatus = 'status';
const String msgAudioAck = 'audio_ack';
const String msgPong = 'pong';

/// 二进制音频帧：4 字节小端序号 + PCM16 单声道 16k 小端采样。
Uint8List packAudio(int seq, Uint8List pcm) {
  final out = Uint8List(4 + pcm.length);
  final view = ByteData.view(out.buffer);
  view.setUint32(0, seq, Endian.little);
  out.setRange(4, out.length, pcm);
  return out;
}
