"""测试夹具与假实现。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import numpy as np
import pytest

from class_helper.config import (
    AppConfig,
    ArchiveConfig,
    AsrConfig,
    DetectionConfig,
    LLMConfig,
    ServerConfig,
)
from class_helper.protocol import TranscriptSegment


@pytest.fixture
def cfg(tmp_path) -> AppConfig:
    return AppConfig(
        server=ServerConfig(host="127.0.0.1", port=8999, token="test-token"),
        llm=LLMConfig(base_url="http://localhost/v1", api_key="k"),
        asr=AsrConfig(model="small", device="cpu", compute="int8", language="zh"),
        detection=DetectionConfig(
            aliases=["陈嘉毅", "嘉毅"],
            trigger_words=["这位同学", "抽一位"],
            context_window_seconds=90,
            cooldown_seconds=2,
            manual_context_seconds=60,
        ),
        archive=ArchiveConfig(root=str(tmp_path / "archives")),
    )


class FakeLLM:
    """鸭子类型：实现 Detector/Responder 用到的方法。"""

    def __init__(self, classify_result: dict | None = None) -> None:
        self.classify_result = classify_result or {"level": "none"}
        self.calls: list[str] = []

    async def complete(self, system: str, user: str, model: str | None = None) -> str:
        self.calls.append(("complete", user))
        return "- 模拟笔记要点"

    async def classify(self, system: str, user: str) -> str:
        self.calls.append(("classify", user))
        return json.dumps(self.classify_result, ensure_ascii=False)

    async def stream_answer(self, system: str, user: str) -> AsyncIterator[str]:
        self.calls.append(("answer", user))
        for chunk in ["- 要点一", "\n- 要点二"]:
            yield chunk


class FakeASR:
    def __init__(self, text_by_call: list[str] | str = "模拟转写内容") -> None:
        if isinstance(text_by_call, str):
            text_by_call = [text_by_call]
        self.texts = list(text_by_call)
        self.loaded = True

    async def load(self) -> str:
        return "fake-asr"

    def transcribe(self, samples: np.ndarray, language: str | None) -> str:
        return self.texts.pop(0) if self.texts else "模拟转写内容"


class EventCollector:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def __call__(self, event: dict) -> None:
        self.events.append(event)

    def of_type(self, type_: str) -> list[dict]:
        return [e for e in self.events if e.get("type") == type_]


def noise_pcm(seconds: float, amplitude: float = 0.2) -> bytes:
    """模拟响亮的语音：高幅值噪声，能量检测判为语音。"""
    rng = np.random.default_rng(42)
    samples = (rng.standard_normal(int(seconds * 16000)) * amplitude * 32768)
    return samples.astype(np.int16).tobytes()


def silence_pcm(seconds: float) -> bytes:
    return np.zeros(int(seconds * 16000), dtype=np.int16).tobytes()


__all__ = [
    "EventCollector",
    "FakeASR",
    "FakeLLM",
    "TranscriptSegment",
    "cfg",
    "noise_pcm",
    "silence_pcm",
]
