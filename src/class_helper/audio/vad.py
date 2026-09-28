"""流式 VAD：silero-vad 增量切分语音段。

输入 PCM16 字节块，输出完成的语音段（float32 数组 + 起止时间）。
长段超过 max_segment_seconds 强制切出，保证转写延迟有上界。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import numpy as np

from class_helper.protocol import AUDIO_RATE

logger = logging.getLogger(__name__)

FRAME_SAMPLES = 512  # silero-vad 16kHz 固定帧长（32ms）


@dataclass
class SpeechSegment:
    start: float  # 会话内相对秒
    end: float
    samples: np.ndarray  # float32 mono 16k


class StreamVAD:
    """状态机：静音 -> 语音（带预滚与拖尾），由 `process` 驱动。"""

    def __init__(
        self,
        model=None,
        *,
        threshold: float = 0.5,
        silence_threshold: float = 0.35,
        hangover_seconds: float = 0.5,
        min_speech_seconds: float = 0.24,
        max_segment_seconds: float = 14.0,
        preroll_seconds: float = 0.12,
    ) -> None:
        self._model = model
        self._threshold = threshold
        self._silence_threshold = silence_threshold
        self._hangover = int(hangover_seconds * AUDIO_RATE / FRAME_SAMPLES)  # 帧数
        self._min_speech = min_speech_seconds
        self._max_segment = max_segment_seconds
        self._preroll = np.zeros(int(preroll_seconds * AUDIO_RATE), dtype=np.float32)

        self._in_speech = False
        self._silence_run = 0
        self._seg_start = 0.0
        self._seg_buf: list[np.ndarray] = []
        self._seg_len = 0
        self._time = 0.0  # 已消费音频总时长（秒）
        self._pending = np.zeros(0, dtype=np.float32)  # 不足一帧的跨块残余

    def process(self, pcm_bytes: bytes) -> list[SpeechSegment]:
        """消费一块 PCM16 字节，返回本次完成的语音段。"""
        if len(pcm_bytes) == 0:
            return []
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        self._pending = np.concatenate([self._pending, samples])
        out: list[SpeechSegment] = []
        n_frames = len(self._pending) // FRAME_SAMPLES
        for i in range(n_frames):
            frame = self._pending[i * FRAME_SAMPLES : (i + 1) * FRAME_SAMPLES]
            out.extend(self._consume_frame(frame))
        self._pending = self._pending[n_frames * FRAME_SAMPLES :]
        return out

    def _consume_frame(self, frame: np.ndarray) -> list[SpeechSegment]:
        self._time += FRAME_SAMPLES / AUDIO_RATE
        prob = self._speech_prob(frame)
        if not self._in_speech:
            if prob >= self._threshold:
                self._in_speech = True
                self._silence_run = 0
                self._seg_start = max(0.0, self._time - FRAME_SAMPLES / AUDIO_RATE)
                self._seg_buf = [self._preroll.copy(), frame]
                self._seg_len = len(self._preroll) + len(frame)
            else:
                self._preroll = np.roll(self._preroll, -len(frame))
                self._preroll[-len(frame) :] = frame
            return []

        self._seg_buf.append(frame)
        self._seg_len += len(frame)
        if prob < self._silence_threshold:
            self._silence_run += 1
        else:
            self._silence_run = 0

        seg_duration = self._seg_len / AUDIO_RATE
        if (self._silence_run >= self._hangover and seg_duration >= self._min_speech) or (
            seg_duration >= self._max_segment
        ):
            return [self._emit()]
        return []

    def _speech_prob(self, frame: np.ndarray) -> float:
        if self._model is None:
            # 无模型时退化为能量检测，保证管线可测
            rms = float(np.sqrt(np.mean(frame**2)))
            return 1.0 if rms > 0.006 else 0.0
        try:
            return float(self._model(frame, AUDIO_RATE))
        except Exception:  # noqa: BLE001 - VAD 失败不应打断录音
            logger.exception("VAD 推理失败，本帧按静音处理")
            return 0.0

    def _emit(self) -> SpeechSegment:
        samples = np.concatenate(self._seg_buf)
        seg = SpeechSegment(
            start=self._seg_start,
            end=self._seg_start + len(samples) / AUDIO_RATE,
            samples=samples,
        )
        self._in_speech = False
        self._silence_run = 0
        self._seg_buf = []
        self._seg_len = 0
        return seg

    def flush(self) -> SpeechSegment | None:
        """会话结束时把残余语音段吐出。"""
        if self._in_speech and self._seg_len / AUDIO_RATE >= self._min_speech:
            return self._emit()
        self._in_speech = False
        self._seg_buf = []
        self._seg_len = 0
        return None


async def load_vad_model():
    """加载 silero-vad ONNX 模型，失败返回 None（退化为能量检测）。"""

    def _load():
        from silero_vad import load_silero_vad

        return load_silero_vad()

    try:
        return await asyncio.to_thread(_load)
    except Exception:  # noqa: BLE001
        logger.exception("silero-vad 加载失败，使用能量检测兜底")
        return None
