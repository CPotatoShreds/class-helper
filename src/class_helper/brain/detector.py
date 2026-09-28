"""点名检测：本地关键词预筛 + LLM 分级确认。"""

from __future__ import annotations

import logging

from class_helper.brain.llm import LLMClient
from class_helper.config import DetectionConfig
from class_helper.protocol import DetectionResult, TranscriptSegment

logger = logging.getLogger(__name__)

DETECT_SYSTEM = """你是大学课堂的实时监课助手。根据老师的讲话转写片段判断点名状态，输出严格 JSON：
{"level": "none|preparing|called", "question": "...", "addressed_to": "...", "quote": "...", "reason": "..."}

- none: 老师没有提问，也没有要点名
- preparing: 老师宣布即将点名或提问（如"我抽个同学来回答""下面提问几个同学"），但还没点具体的人
- called: 老师已经点到具体的学生（读到姓名、称呼或方位描述，如"第三排那位同学""穿白色衣服的"），正等待其回答

question：老师提出的问题原文（没有则为空字符串）；addressed_to：被点到的对象描述；quote：触发判定的原句；reason：一句话理由。
只输出 JSON，不要任何其他内容。嘈杂环境转写可能有错别字，注意谐音。"""


def _to_hans(text: str) -> str:
    """whisper 常输出繁体，统一转简体再做匹配。"""
    try:
        from zhconv import convert

        return convert(text, "zh-hans")
    except ImportError:  # pragma: no cover
        return text


class Detector:
    def __init__(self, cfg: DetectionConfig, llm: LLMClient) -> None:
        self._cfg = cfg
        self._llm = llm
        self.aliases = [_to_hans(a) for a in cfg.aliases]
        self._triggers = [_to_hans(w) for w in cfg.trigger_words]

    def prefilter_hit(self, text: str) -> tuple[bool, str | None]:
        """返回 (是否命中, 命中的别名)。命中别名时可直接判 called，无需 LLM。"""
        text = _to_hans(text)
        for alias in self.aliases:
            if alias and alias in text:
                return True, alias
        if self._triggers and any(w and w in text for w in self._triggers):
            return True, None
        return False, None

    def alias_hit(self, text: str) -> str | None:
        for alias in self.aliases:
            if alias and alias in text:
                return alias
        return None

    async def classify(self, segments: list[TranscriptSegment]) -> DetectionResult:
        """把最近上下文交给 LLM 分级。"""
        lines = [f"[{int(seg.t0)}s-{int(seg.t1)}s] {seg.text}" for seg in segments]
        user = (
            f"学生姓名/别名单（老师点到即视为 called）：{'、'.join(self.aliases)}\n\n"
            "老师最近讲话转写（旧→新）：\n" + "\n".join(lines)
        )
        raw = await self._llm.classify(DETECT_SYSTEM, user)
        result = DetectionResult.from_json(raw)
        logger.info("检测分级: %s (%s)", result.level, result.reason)
        return result
