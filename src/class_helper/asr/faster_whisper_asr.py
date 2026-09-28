"""faster-whisper ASR 封装：设备/精度自适应，GPU 不可用自动回退 CPU。"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import numpy as np

from class_helper.config import AsrConfig

logger = logging.getLogger(__name__)


def _prepare_windows_cuda() -> list[Path]:
    """best-effort：把 pip 安装的 nvidia cu12 DLL 目录加入 DLL 搜索路径。"""
    added: list[Path] = []
    if sys.platform != "win32":
        return added
    try:
        import nvidia  # noqa: F401  仅探测是否以 pip 包装了 CUDA 组件
    except ImportError:
        return added
    import importlib

    for pkg in ("nvidia.cublas", "nvidia.cudnn"):
        try:
            mod = importlib.import_module(pkg)
            bin_dir = Path(mod.__file__).parent / "bin"
            if bin_dir.is_dir():
                os.add_dll_directory(str(bin_dir))
                added.append(bin_dir)
        except (ImportError, OSError):
            continue
    return added


def resolve_asr_plan(cfg: AsrConfig) -> tuple[str, str, str]:
    """决定 (model, device, compute)。auto 规则：GPU→medium/float16，CPU→small/int8。"""
    model = cfg.model
    device = cfg.device
    compute = cfg.compute
    if device == "auto":
        device = "cpu"
        try:
            import ctranslate2

            if ctranslate2.get_cuda_device_count() > 0:
                device = "cuda"
        except Exception:  # noqa: BLE001
            logger.warning("探测 CUDA 失败，按 CPU 处理", exc_info=True)
    if compute == "auto":
        compute = "float16" if device == "cuda" else "int8"
    if model == "auto":
        model = "medium" if device == "cuda" else "small"
    return model, device, compute


class WhisperASR:
    """懒加载模型；transcribe 为阻塞调用，请在线程池中使用。"""

    def __init__(self, cfg: AsrConfig) -> None:
        self._cfg = cfg
        self._model = None
        self._ready = asyncio.Event()
        self._load_error: Exception | None = None
        self.plan: tuple[str, str, str] | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def describe(self) -> str:
        if not self.plan:
            return "未加载"
        model, device, compute = self.plan
        return f"faster-whisper {model} / {device} / {compute}"

    async def load(self) -> str:
        """加载模型，返回实际使用的描述（供界面显示与日志）。"""
        if self._model is not None:
            return self.describe()
        try:
            desc = await asyncio.to_thread(self._load_sync)
        except Exception as exc:
            self._load_error = exc
            raise
        self._ready.set()
        return desc

    async def wait_ready(self, timeout: float = 600) -> bool:
        """等待模型就绪（加载中/下载中的调用方在此挂起，避免丢语音段）。"""
        if self._load_error:
            return False
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
        except TimeoutError:
            return False
        return True

    def _load_sync(self) -> str:
        from faster_whisper import WhisperModel

        self.plan = resolve_asr_plan(self._cfg)
        model, device, compute = self.plan
        _prepare_windows_cuda()
        try:
            self._model = WhisperModel(model, device=device, compute_type=compute)
        except Exception as exc:
            if device == "cuda":
                logger.warning("CUDA 初始化失败(%s)，回退 CPU int8", exc)
                device, compute = "cpu", "int8"
                if model == "medium":
                    model = "small"  # CPU 上 medium 太慢
                self.plan = (model, device, compute)
                self._model = WhisperModel(model, device=device, compute_type=compute)
            else:
                raise
        logger.info("ASR 就绪: %s", self.describe())
        return self.describe()

    def transcribe(self, samples: np.ndarray, language: str | None) -> str:
        """阻塞转写一段 float32 16k 音频，返回拼接文本。"""
        assert self._model is not None, "模型尚未加载"
        segments, _info = self._model.transcribe(
            samples,
            language=language or None,
            beam_size=1,
            best_of=1,
            temperature=0.0,
            condition_on_previous_text=False,
            vad_filter=False,  # 上游已做流式 VAD
            no_speech_threshold=0.5,
        )
        return "".join(seg.text for seg in segments).strip()
