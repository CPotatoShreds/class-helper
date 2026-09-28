"""配置加载与校验。"""

from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ASR_MODELS = {"tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"}


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8765
    token: str = "change-me-please"


@dataclass
class LLMConfig:
    base_url: str = ""
    api_key: str = ""
    detection_model: str = "glm-4-flash"
    answer_model: str = "glm-4-plus"
    timeout_seconds: float = 20.0


@dataclass
class AsrConfig:
    model: str = "auto"
    device: str = "auto"
    compute: str = "auto"
    language: str = "zh"


@dataclass
class DetectionConfig:
    aliases: list[str] = field(default_factory=lambda: ["陈嘉毅"])
    trigger_words: list[str] = field(default_factory=list)
    context_window_seconds: int = 90
    cooldown_seconds: int = 30
    manual_context_seconds: int = 120


@dataclass
class ArchiveConfig:
    root: str = "archives"
    keep_audio: bool = True
    summary_interval_seconds: int = 300
    summary_interval_chars: int = 800


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    archive: ArchiveConfig = field(default_factory=ArchiveConfig)

    @property
    def archive_root(self) -> Path:
        return Path(self.archive.root).resolve()


def _merge(dc: Any, data: dict[str, Any] | None = None) -> Any:
    """把 dict 递归合入 dataclass；仅接受已知字段，未知字段报错提示拼写问题。"""
    for key, value in (data or {}).items():
        if not hasattr(dc, key):
            raise ValueError(f"配置项未知: `{key}`（检查 config.toml 是否拼写正确）")
        current = getattr(dc, key)
        if dataclasses.is_dataclass(current):
            if not isinstance(value, dict):
                raise ValueError(f"配置节 `{key}` 必须是 [table] 形式")
            _merge(current, value)
        else:
            setattr(dc, key, value)
    return dc


def load_config(path: str | Path | None = None) -> AppConfig:
    """加载配置。path 为空时依次尝试 ./config.toml，不存在则全默认。"""
    cfg = AppConfig()
    if path is None:
        path = Path("config.toml")
        if not path.exists():
            return cfg
    else:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"配置文件不存在: {path}")
    with path.open("rb") as f:
        data = tomllib.load(f)
    _merge(cfg, data)
    _validate(cfg)
    return cfg


def _validate(cfg: AppConfig) -> None:
    if cfg.llm.base_url and not cfg.llm.api_key:
        raise ValueError("配置了 llm.base_url 但缺少 llm.api_key")
    if cfg.asr.model != "auto" and cfg.asr.model not in ASR_MODELS:
        raise ValueError(f"asr.model 必须是 auto 或 {sorted(ASR_MODELS)}")
    if cfg.asr.device not in {"auto", "cpu", "cuda"}:
        raise ValueError("asr.device 必须是 auto / cpu / cuda")
    if cfg.asr.compute not in {"auto", "int8", "float16", "float32"}:
        raise ValueError("asr.compute 必须是 auto / int8 / float16 / float32")
    if cfg.server.port <= 0 or cfg.server.port > 65535:
        raise ValueError("server.port 非法")
    if not cfg.detection.aliases:
        raise ValueError("detection.aliases 至少配置一个你的名字/别名")
