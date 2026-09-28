"""WS 协议定义：客户端与服务端之间的消息类型。

文本帧一律为 JSON，含 `type` 字段；二进制帧为音频分片：
前 4 字节小端 uint32 序号，其余为 PCM16 单声道 16kHz 小端采样。
"""

from __future__ import annotations

import json
import struct
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from typing import Any

AUDIO_RATE = 16000
AUDIO_CHANNELS = 1
AUDIO_SAMPLE_WIDTH = 2  # PCM16
SEQ_STRUCT = struct.Struct("<I")
HEADER_SIZE = SEQ_STRUCT.size


# 客户端 -> 服务端
MSG_AUTH = "auth"
MSG_START_SESSION = "start_session"
MSG_STOP_SESSION = "stop_session"
MSG_MANUAL_ALERT = "manual_alert"
MSG_PING = "ping"

# 服务端 -> 客户端
MSG_AUTH_OK = "auth_ok"
MSG_ERROR = "error"
MSG_SESSION_STARTED = "session_started"
MSG_SESSION_STOPPED = "session_stopped"
MSG_TRANSCRIPT = "transcript"
MSG_ALERT = "alert"
MSG_ANSWER_DELTA = "answer_delta"
MSG_ANSWER_DONE = "answer_done"
MSG_STATUS = "status"
MSG_AUDIO_ACK = "audio_ack"
MSG_PONG = "pong"


def pack_audio(seq: int, pcm: bytes) -> bytes:
    return SEQ_STRUCT.pack(seq) + pcm


def unpack_audio(frame: bytes) -> tuple[int, bytes]:
    if len(frame) < HEADER_SIZE:
        raise ValueError("音频帧过短，缺少序号头")
    (seq,) = SEQ_STRUCT.unpack_from(frame)
    return seq, frame[HEADER_SIZE:]


def encode_msg(type_: str, **fields: Any) -> str:
    return json.dumps({"type": type_, **fields}, ensure_ascii=False)


def decode_msg(raw: str | bytes) -> dict[str, Any]:
    data = json.loads(raw)
    if not isinstance(data, dict) or "type" not in data:
        raise ValueError("消息必须是含 type 字段的 JSON 对象")
    return data


@dataclass
class TranscriptSegment:
    """一段转写结果。t0/t1 为会话内相对秒数。"""

    t0: float
    t1: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def duration(self) -> float:
        return self.t1 - self.t0


@dataclass
class DetectionResult:
    level: str  # none / preparing / called
    question: str = ""
    addressed_to: str = ""
    quote: str = ""
    reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, text: str) -> DetectionResult:
        """容错解析 LLM 输出：截取首个 {...} 块。"""
        text = text.strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError(f"LLM 输出不含 JSON: {text[:200]}")
        import json

        data = json.loads(text[start : end + 1])
        level = data.get("level", "none")
        if level not in {"none", "preparing", "called"}:
            level = "none"
        return cls(
            level=level,
            question=str(data.get("question", "")).strip(),
            addressed_to=str(data.get("addressed_to", "")).strip(),
            quote=str(data.get("quote", "")).strip(),
            reason=str(data.get("reason", "")).strip(),
            raw=data,
        )


def iter_lines(text: str) -> Iterator[str]:
    """把速答文本拆成要点行（App/日志展示用）。"""
    for line in text.splitlines():
        line = line.strip().lstrip("-•*").strip()
        if line:
            yield line
