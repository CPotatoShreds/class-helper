"""转写存档：transcript.jsonl 逐段落盘 + 内存副本供检测/速答/归档使用。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from class_helper.protocol import TranscriptSegment


def wall_fmt(start: datetime, t: float) -> str:
    """会话内相对秒 -> 墙上时钟 HH:MM。"""
    return (start + timedelta(seconds=t)).strftime("%H:%M")


class TranscriptStore:
    def __init__(self, path: Path, start: datetime) -> None:
        self.path = path
        self.start = start
        self.segments: list[TranscriptSegment] = []
        self._fh = path.open("a", encoding="utf-8")

    def add(self, seg: TranscriptSegment) -> str:
        """落盘并加入内存，返回对应的墙上时钟 HH:MM。"""
        self.segments.append(seg)
        self._fh.write(
            json.dumps(
                {
                    "t0": round(seg.t0, 2),
                    "t1": round(seg.t1, 2),
                    "wall": wall_fmt(self.start, seg.t0),
                    "text": seg.text,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        self._fh.flush()
        return wall_fmt(self.start, seg.t0)

    def since(self, index: int) -> list[TranscriptSegment]:
        return self.segments[index:]

    def recent(self, seconds: float, up_to: float | None = None) -> list[TranscriptSegment]:
        """最近 seconds 秒的段（可选截至某个时间点）。"""
        end = up_to if up_to is not None else (self.segments[-1].t1 if self.segments else 0.0)
        return [s for s in self.segments if s.t1 > end - seconds]

    def context_text(self, segments: list[TranscriptSegment]) -> str:
        return "\n".join(f"[{wall_fmt(self.start, s.t0)}] {s.text}" for s in segments)

    def all_text(self) -> str:
        return self.context_text(self.segments)

    def close(self) -> None:
        self._fh.close()
