"""ASR 抽象接口，便于替换云端实现。"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class ASREngine(ABC):
    @abstractmethod
    async def load(self) -> str:
        """加载模型，返回描述信息。"""

    @abstractmethod
    def transcribe(self, samples: np.ndarray, language: str | None) -> str:
        """阻塞转写 float32 16k 音频。"""
