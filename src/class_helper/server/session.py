"""课程会话：编排 音频→VAD→ASR→检测/速答→归档 的整条流水线。"""

from __future__ import annotations

import asyncio
import logging
import time
import wave
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

from class_helper.archive.notes import NoteWriter
from class_helper.archive.transcript import TranscriptStore, wall_fmt
from class_helper.asr.base import ASREngine
from class_helper.audio.vad import StreamVAD
from class_helper.brain.detector import Detector
from class_helper.brain.llm import LLMClient
from class_helper.brain.responder import Responder
from class_helper.config import AppConfig
from class_helper.protocol import (
    MSG_ALERT,
    MSG_ANSWER_DELTA,
    MSG_ANSWER_DONE,
    MSG_TRANSCRIPT,
    DetectionResult,
    TranscriptSegment,
)

logger = logging.getLogger(__name__)

EmitFn = Callable[[dict], Awaitable[None]]


class ClassSession:
    """单场课程会话。生命周期：run() → push_audio()*N → stop()。"""

    def __init__(
        self,
        course: str,
        cfg: AppConfig,
        llm: LLMClient,
        asr: ASREngine,
        emit: EmitFn,
        vad_model=None,
    ) -> None:
        self.course = course
        self.cfg = cfg
        self.llm = llm
        self.asr = asr
        self.emit = emit
        self.started_at = datetime.now()
        self.session_dir = self._make_session_dir()
        self.store = TranscriptStore(self.session_dir / "transcript.jsonl", self.started_at)
        self.notes = NoteWriter(self.session_dir, self.store, llm, cfg.archive)
        self.detector = Detector(cfg.detection, llm)
        self.responder = Responder(llm)
        self.vad = StreamVAD(vad_model)

        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=2048)
        self._pending_frames: dict[int, bytes] = {}
        self._expected_seq = 0
        self._wav: wave.Wave_write | None = None
        if cfg.archive.keep_audio:
            # 句柄刻意与会话同生命周期（stop() 中关闭）
            self._wav = wave.open(str(self.session_dir / "audio.wav"), "wb")  # noqa: SIM115
            self._wav.setnchannels(1)
            self._wav.setsampwidth(2)
            self._wav.setframerate(16000)

        self._workers: list[asyncio.Task] = []
        self._answer_task: asyncio.Task | None = None
        self._detect_task: asyncio.Task | None = None
        self._closed = False
        self._start_monotonic = time.monotonic()
        self._last_alert: tuple[str, float] = ("none", 0.0)  # (级别, monotonic)
        self.current_question = ""
        self.current_answer = ""

    # ---------- 生命周期 ----------

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def session_id(self) -> str:
        return self.session_dir.name

    def _make_session_dir(self) -> Path:
        base = self.cfg.archive_root / self._safe_name(self.course) / self.started_at.strftime("%Y-%m-%d")
        session_dir = base / self.started_at.strftime("%H%M%S")
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    @staticmethod
    def _safe_name(name: str) -> str:
        bad = '<>:"/\\|?*'
        return "".join("_" if c in bad else c for c in name).strip() or "未命名课程"

    async def run(self) -> None:
        self._workers = [
            asyncio.create_task(self._audio_worker(), name="audio-worker"),
            asyncio.create_task(self._summarizer_timer(), name="summarizer-timer"),
        ]
        await self.emit(
            {
                "type": "session_started",
                "course": self.course,
                "session_id": self.session_id,
                "started_at": self.started_at.isoformat(timespec="seconds"),
            }
        )

    def push_audio(self, pcm: bytes) -> None:
        if self._closed or not pcm:
            return
        if self._wav is not None:
            self._wav.writeframes(pcm)
        try:
            self._audio_queue.put_nowait(pcm)
        except asyncio.QueueFull:
            logger.warning("音频队列满，丢弃一块（%d 字节）", len(pcm))

    def push_audio_seq(self, seq: int, pcm: bytes) -> None:
        """按序号收音频：缓存乱序/缺口，按序写入；返回可确认的最大连续序号。"""
        if seq < self._expected_seq:  # 重发重复帧
            return
        self._pending_frames[seq] = pcm
        while self._expected_seq in self._pending_frames:
            frame = self._pending_frames.pop(self._expected_seq)
            self.push_audio(frame)
            self._expected_seq += 1
        if len(self._pending_frames) > 8192:  # 缺口始终未补上的保护
            logger.warning("音频补传缓冲超限，放弃 %d 帧", len(self._pending_frames))
            self._pending_frames.clear()
            self._expected_seq = seq + 1

    def ack_seq(self) -> int:
        """当前已连续收到的最大序号（客户端据此重发缺口）。"""
        return self._expected_seq - 1

    async def stop(self) -> dict:
        if self._closed:
            return {"summary_path": str(self.notes.summary_path)}
        self._closed = True
        # 把残余语音段吐给 ASR
        for seg in self._drain_audio_queue():
            await self._handle_segment(seg, final=True)
        if (last := self.vad.flush()) is not None:
            await self._handle_segment(last, final=True)
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        await self._cancel_transient_tasks()
        self.store.close()
        if self._wav is not None:
            self._wav.close()
            self._wav = None
        summary_path = await self.notes.final_summary(self.course)
        result = {
            "course": self.course,
            "transcripts": len(self.store.segments),
            "summary_path": str(summary_path) if summary_path else "",
        }
        await self.emit({"type": "session_stopped", **result})
        return result

    # ---------- 音频 → 转写 ----------

    def _drain_audio_queue(self):
        out = []
        while not self._audio_queue.empty():
            chunk = self._audio_queue.get_nowait()
            if chunk is None:
                continue
            out.extend(self.vad.process(chunk))
        return out

    async def _audio_worker(self) -> None:
        while True:
            pcm = await self._audio_queue.get()
            if pcm is None or self._closed:
                continue
            try:
                segments = await asyncio.to_thread(self.vad.process, pcm)
            except Exception:  # noqa: BLE001
                logger.exception("VAD 处理失败")
                continue
            for seg in segments:
                await self._handle_segment(seg)

    async def _handle_segment(self, seg, final: bool = False) -> None:
        # 模型加载/下载中：等待而不是丢弃，避免开局丢课
        if not self.asr.loaded and not await self.asr.wait_ready(timeout=30 if final else 600):
            logger.warning("ASR 未就绪，丢弃一段语音（%.1fs）", seg.end - seg.start)
            return
        try:
            text = await asyncio.to_thread(self.asr.transcribe, seg.samples, self.cfg.asr.language)
        except Exception:  # noqa: BLE001
            logger.exception("转写失败")
            return
        if not text:
            return
        segment = TranscriptSegment(t0=seg.start, t1=seg.end, text=text)
        wall = self.store.add(segment)
        self.notes.count_chars(text)
        logger.info("[%s] %s", wall, text)
        await self.emit(
            {
                "type": MSG_TRANSCRIPT,
                **segment.to_dict(),
                "wall": wall,
                "final": final,
            }
        )
        await self._consider_detection(segment)
        await self.notes.maybe_summarize()

    # ---------- 检测与速答 ----------

    def _cooldown_ok(self, level: str) -> bool:
        last_level, last_ts = self._last_alert
        if level == "none":
            return True
        return not (last_level == level and time.monotonic() - last_ts < self.cfg.detection.cooldown_seconds)

    def _mark_alert(self, level: str) -> None:
        self._last_alert = (level, time.monotonic())

    async def _consider_detection(self, segment: TranscriptSegment) -> None:
        hit, alias = self.detector.prefilter_hit(segment.text)
        if not hit:
            return
        if alias:  # 名字直接命中，最高优先级，免 LLM 确认
            await self._trigger_alert(
                DetectionResult(level="called", addressed_to=alias, quote=segment.text),
                force=True,
            )
            return
        if not self._cooldown_ok("detect"):
            return
        self._mark_alert("detect")
        if self._detect_task and not self._detect_task.done():
            return  # 已有检测在跑
        window = self.store.recent(self.cfg.detection.context_window_seconds)
        self._detect_task = asyncio.create_task(self._run_detection(list(window)))

    async def _run_detection(self, window: list[TranscriptSegment]) -> None:
        try:
            result = await self.detector.classify(window)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM 分级失败: %s", exc)
            return
        if result.level == "none":
            return
        await self._trigger_alert(result)

    async def _trigger_alert(self, result: DetectionResult, force: bool = False) -> None:
        level = result.level
        if level == "none":
            return
        if not force and not self._cooldown_ok(level):
            logger.info("告警冷却中，忽略 %s", level)
            return
        if level == "called":
            self._mark_alert("called")
        elif level == "preparing":
            self._mark_alert("preparing")
        await self.emit(
            {
                "type": MSG_ALERT,
                "level": level,
                "question": result.question,
                "addressed_to": result.addressed_to,
                "quote": result.quote,
                "reason": result.reason,
                "wall": wall_fmt(self.started_at, self.store.segments[-1].t1 if self.store.segments else 0.0),
            }
        )
        if level == "called":
            question = result.question
            if not question:
                # 从最近上下文里捞最后几句，速答 prompt 允许其自行推测问题
                question = ""
            await self._start_answer(question)

    async def _start_answer(self, question: str) -> None:
        await self._cancel_answer_task()
        self.current_question = question
        window = self.store.recent(max(self.cfg.detection.context_window_seconds, 120.0))
        context_text = self.store.context_text(window)
        self._answer_task = asyncio.create_task(self._answer_worker(question, context_text))

    async def manual_alert(self) -> None:
        """用户手动触发：绕过检测，直接定位问题并速答。"""
        window = self.store.recent(self.cfg.detection.manual_context_seconds)
        question = ""
        if window and self.llm is not None:
            try:
                result = await self.detector.classify(list(window))
                question = result.question
                await self.emit(
                    {
                        "type": MSG_ALERT,
                        "level": "called",
                        "question": question,
                        "addressed_to": "（手动触发）",
                        "quote": result.quote,
                        "reason": result.reason,
                        "wall": wall_fmt(self.started_at, window[-1].t1),
                    }
                )
            except Exception:  # noqa: BLE001
                logger.exception("手动触发的问题提取失败，直接进入速答")
                await self.emit(
                    {
                        "type": MSG_ALERT,
                        "level": "called",
                        "question": "",
                        "addressed_to": "（手动触发）",
                        "quote": "",
                        "reason": "",
                        "wall": wall_fmt(self.started_at, window[-1].t1 if window else 0.0),
                    }
                )
        if not self._answer_task or self._answer_task.done():
            context_text = self.store.context_text(window)
            self._answer_task = asyncio.create_task(self._answer_worker(question, context_text))

    async def _answer_worker(self, question: str, context_text: str) -> None:
        parts: list[str] = []
        try:
            async for delta in self.responder.answer_stream(question, context_text):
                parts.append(delta)
                await self.emit({"type": MSG_ANSWER_DELTA, "text": delta})
        except Exception as exc:  # noqa: BLE001
            logger.exception("速答失败")
            await self.emit({"type": MSG_ANSWER_DELTA, "text": f"\n（速答失败：{exc}）"})
        answer = "".join(parts).strip()
        self.current_answer = answer
        await self.emit({"type": MSG_ANSWER_DONE, "question": question, "answer": answer})
        self._save_qa(question, answer)

    def _save_qa(self, question: str, answer: str) -> None:
        try:
            wall = wall_fmt(self.started_at, self.store.segments[-1].t1 if self.store.segments else 0.0)
            with (self.session_dir / "qa.md").open("a", encoding="utf-8") as f:
                f.write(f"\n## {wall} 老师的问题\n\n{question or '（未捕捉到原话）'}\n\n## 速答\n\n{answer}\n")
        except OSError:
            logger.warning("qa.md 写入失败", exc_info=True)

    async def _summarizer_timer(self) -> None:
        while True:
            await asyncio.sleep(30)
            await self.notes.maybe_summarize()

    async def _cancel_answer_task(self) -> None:
        if self._answer_task and not self._answer_task.done():
            self._answer_task.cancel()
            await asyncio.gather(self._answer_task, return_exceptions=True)

    async def _cancel_transient_tasks(self) -> None:
        for task in (self._answer_task, self._detect_task):
            if task and not task.done():
                task.cancel()
        await asyncio.gather(
            *(t for t in (self._answer_task, self._detect_task) if t), return_exceptions=True
        )

    # ---------- 状态 ----------

    def status(self) -> dict:
        return {
            "course": self.course,
            "session_id": self.session_id,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "elapsed_seconds": round(time.monotonic() - self._start_monotonic, 1),
            "transcripts": len(self.store.segments),
            "session_dir": str(self.session_dir),
        }
