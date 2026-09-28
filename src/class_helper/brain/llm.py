"""LLM 客户端：云端走 OpenAI SDK；本机 Ollama 走标准库直连。

本环境（Windows + httpx2 分支 + 系统代理/安全软件）下 httpx 客户端对
127.0.0.1 的请求会随机挂死，故 loopback 路径用 urllib + 空 ProxyHandler，
行为与 curl 完全一致。
"""

import asyncio
import json
import logging
import threading
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

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

_NO_THINK = {"reasoning_effort": "none"}  # 思维链模型（qwen3.5 等）关思考保延迟


def _is_loopback(base_url: str) -> bool:
    host = urlparse(base_url).hostname if base_url else None
    return host in {"127.0.0.1", "localhost", "::1"}


class LLMClient:
    """检测与速答可分别配置模型；瞬时错误自动重试一次。"""

    def __init__(self, cfg: LLMConfig) -> None:
        self._cfg = cfg
        self._loopback = _is_loopback(cfg.base_url)
        self._client: AsyncOpenAI | None = None
        if not self._loopback:
            self._client = AsyncOpenAI(
                base_url=cfg.base_url or None,
                api_key=cfg.api_key or "empty",
                timeout=cfg.timeout_seconds,
                max_retries=0,  # 自己做重试，避免 SDK 默认 2 次叠加超时
            )
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # ---------- 标准库直连（loopback / Ollama） ----------

    def _post(self, payload: dict[str, Any], timeout: float):
        url = self._cfg.base_url.rstrip("/") + "/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._opener.open(req, timeout=timeout)

    def _stdlib_complete(self, model: str, system: str, user: str) -> str:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "stream": False,
            **_NO_THINK,
        }
        try:
            with self._post(payload, self._cfg.timeout_seconds + 30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"LLM HTTP {exc.code}: {exc.read()[:200]}") from exc
        return data["choices"][0]["message"].get("content") or ""

    def _stdlib_stream(
        self,
        model: str,
        system: str,
        user: str,
        out: asyncio.Queue,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        def emit(item: Any) -> None:
            loop.call_soon_threadsafe(out.put_nowait, item)

        def worker() -> None:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.3,
                "stream": True,
                **_NO_THINK,
            }
            try:
                with self._post(payload, self._cfg.timeout_seconds + 60) as resp:
                    for raw in resp:
                        line = raw.decode("utf-8", errors="ignore").strip()
                        if not line.startswith("data: "):
                            continue
                        body = line[len("data: ") :]
                        if body == "[DONE]":
                            break
                        delta = json.loads(body)["choices"][0]["delta"].get("content")
                        if delta:
                            emit(delta)
            except Exception as exc:  # noqa: BLE001 - 线程内异常送回主循环
                emit({"__error__": str(exc)})
            finally:
                emit(None)

        threading.Thread(target=worker, daemon=True).start()

    # ---------- 公共接口 ----------

    async def complete(self, system: str, user: str, model: str | None = None) -> str:
        """非流式补全；model 缺省用速答模型（总结/归档等质量优先任务共用）。"""
        model = model or self._cfg.answer_model
        if self._loopback:
            return await asyncio.to_thread(self._stdlib_complete, model, system, user)

        assert self._client is not None
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                resp = await self._client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    temperature=0.2,
                    extra_body=_NO_THINK,
                )
                return resp.choices[0].message.content or ""
            except _RETRYABLE as exc:
                last_exc = exc
                logger.warning("LLM 调用失败(第%d次): %s", attempt + 1, exc)
                await asyncio.sleep(1.0)
        raise RuntimeError(f"LLM 调用失败: {last_exc}") from last_exc

    async def classify(self, system: str, user: str) -> str:
        return await self.complete(system, user, model=self._cfg.detection_model)

    async def stream_answer(self, system: str, user: str) -> AsyncIterator[str]:
        """流式产出速答文本；失败时重试一次（重试会重新开始流）。"""
        if self._loopback:
            q: asyncio.Queue = asyncio.Queue()
            self._stdlib_stream(
                self._cfg.answer_model, system, user, q, asyncio.get_running_loop()
            )
            while True:
                item = await q.get()
                if item is None:
                    return
                if isinstance(item, dict):
                    raise RuntimeError(f"速答流失败: {item['__error__']}")
                yield item

        assert self._client is not None
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
                    extra_body=_NO_THINK,
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
