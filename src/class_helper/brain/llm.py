"""OpenAI 兼容 LLM 客户端。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from class_helper.config import LLMConfig

logger = logging.getLogger(__name__)

_RETRYABLE = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)


class LLMClient:
    """检测与速答可分别配置模型；瞬时错误自动重试一次。"""

    def __init__(self, cfg: LLMConfig) -> None:
        self._cfg = cfg
        self._client = AsyncOpenAI(
            base_url=cfg.base_url or None,
            api_key=cfg.api_key or "empty",
            timeout=cfg.timeout_seconds,
            max_retries=0,  # 自己做重试，避免 SDK 默认 2 次叠加超时
        )

    async def complete(self, system: str, user: str, model: str | None = None) -> str:
        """非流式补全；model 缺省用速答模型（总结/归档等质量优先任务共用）。"""
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                resp = await self._client.chat.completions.create(
                    model=model or self._cfg.answer_model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=0.2,
                )
                return resp.choices[0].message.content or ""
            except _RETRYABLE as exc:
                last_exc = exc
                logger.warning("LLM 调用失败(第%d次): %s", attempt + 1, exc)
                await asyncio.sleep(1.0)
        raise RuntimeError(f"LLM 调用失败: {last_exc}") from last_exc

    async def classify(self, system: str, user: str) -> str:
        return await self.complete(system, user, model=self._cfg.detection_model)

    async def stream_answer(
        self, system: str, user: str
    ) -> AsyncIterator[str]:
        """流式产出速答文本；失败时重试一次（重试会重新开始流）。"""
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                stream = await self._client.chat.completions.create(
                    model=self._cfg.answer_model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=0.3,
                    stream=True,
                )
                async for chunk in stream:
                    delta = chunk.choices[0].delta.content if chunk.choices else None
                    if delta:
                        yield delta
                return
            except _RETRYABLE as exc:
                last_exc = exc
                logger.warning("速答流失败(第%d次): %s", attempt + 1, exc)
                await asyncio.sleep(1.0)
        raise RuntimeError(f"速答流失败: {last_exc}") from last_exc
