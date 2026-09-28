"""笔记生成器：增量小结追加 notes.md，课后生成 session_summary.md。"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from class_helper.archive.transcript import TranscriptStore, wall_fmt
from class_helper.brain.llm import LLMClient
from class_helper.config import ArchiveConfig

logger = logging.getLogger(__name__)

INCREMENTAL_SYSTEM = """你是课堂笔记助手。给你课堂转写的新片段（附墙上时间）与此前笔记的结尾，请把新内容整理成复习用要点笔记：
- 简洁中文要点，每行以"- "开头，保留术语、公式描述、例子、老师强调的内容和作业/考试提示
- 输出 3-8 行，不要加标题（时间小节标题由系统添加），不要复述已有笔记
- 转写有错别字与同音字，按语境纠正"""

FINAL_SYSTEM = """你是课堂笔记助手。根据整节课的完整转写，生成本节课的结构化复习总结，Markdown 格式：

# {course} {date} 复习总结

## 本节主题
（1-3 行）

## 核心要点
（分点，保留细节与例子）

## 关键概念/术语
（列表）

## 可能的考点
（列表）

## 作业与提醒
（转写中未提及就写"未提及"）

忠实于转写内容，不要编造；转写错别字按语境纠正。"""


class NoteWriter:
    def __init__(
        self,
        session_dir: Path,
        store: TranscriptStore,
        llm: LLMClient | None,
        cfg: ArchiveConfig,
    ) -> None:
        self.dir = session_dir
        self.store = store
        self.llm = llm
        self.cfg = cfg
        self.notes_path = session_dir / "notes.md"
        self.summary_path = session_dir / "session_summary.md"
        self._summarized_index = 0
        self._pending_chars = 0
        self._last_summary_ts = time.monotonic()
        self._summarizing = False

    async def maybe_summarize(self, *, force: bool = False) -> bool:
        """达到阈值时对新增转写做增量小结，追加进 notes.md。返回是否执行了。"""
        if self._summarizing or self.llm is None:
            return False
        elapsed_ok = time.monotonic() - self._last_summary_ts >= self.cfg.summary_interval_seconds
        if not ((force and self._pending_chars > 0) or (elapsed_ok and self._pending_chars >= self.cfg.summary_interval_chars)):
            return False
        new_segments = self.store.since(self._summarized_index)
        if not new_segments:
            self._last_summary_ts = time.monotonic()
            return False
        self._summarizing = True
        try:
            head = ""
            if self.notes_path.exists():
                head = self.notes_path.read_text(encoding="utf-8")[-1200:]
            user = (
                f"此前笔记结尾（供衔接，勿复述）：\n{head or '（尚无笔记）'}\n\n"
                "新转写片段：\n" + self.store.context_text(new_segments)
            )
            content = (await self.llm.complete(
                INCREMENTAL_SYSTEM,
                user,
            )).strip()
            t0 = wall_fmt(self.store.start, new_segments[0].t0)
            t1 = wall_fmt(self.store.start, new_segments[-1].t1)
            section = f"\n\n## {t0}–{t1}\n\n{content}\n"
            with self.notes_path.open("a", encoding="utf-8") as f:
                if not self.notes_path.exists() or self.notes_path.stat().st_size == 0:
                    f.write(f"# {self.store.start.strftime('%Y-%m-%d')} 课堂笔记\n")
                f.write(section)
            self._summarized_index = len(self.store.segments)
            self._pending_chars = 0
            self._last_summary_ts = time.monotonic()
            logger.info("增量小结完成: %s–%s", t0, t1)
            return True
        except Exception:  # noqa: BLE001
            logger.exception("增量小结失败，稍后重试")
            return False
        finally:
            self._summarizing = False

    def count_chars(self, text: str) -> None:
        self._pending_chars += len(text)

    async def final_summary(self, course: str) -> Path | None:
        """课后总结。LLM 不可用时至少写出原始转写的指引。"""
        if not self.store.segments:
            return None
        if self.llm is None:
            self.summary_path.write_text(
                f"# {course} 复习总结\n\n（LLM 未配置，仅存原始转写）\n",
                encoding="utf-8",
            )
            return self.summary_path
        try:
            await self.maybe_summarize(force=True)
            user = self.store.all_text()
            content = (await self.llm.complete(
                FINAL_SYSTEM.replace("{course}", course).replace(
                    "{date}", self.store.start.strftime("%Y-%m-%d")
                ),
                user,
            )).strip()
            self.summary_path.write_text(content + "\n", encoding="utf-8")
            return self.summary_path
        except Exception:  # noqa: BLE001
            logger.exception("课后总结生成失败")
            return None

    @property
    def last_summary_age(self) -> float:
        return time.monotonic() - self._last_summary_ts
