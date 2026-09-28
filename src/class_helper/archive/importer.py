"""课后导入：把手机录音机等来源的音频文件走同一套 ASR+归档管道。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

import numpy as np

from class_helper.archive.notes import NoteWriter
from class_helper.archive.transcript import TranscriptStore
from class_helper.asr.base import ASREngine
from class_helper.brain.llm import LLMClient
from class_helper.config import AppConfig
from class_helper.protocol import TranscriptSegment

logger = logging.getLogger(__name__)

MAX_SEGMENT_SECONDS = 15.0


async def decode_audio(path: Path) -> np.ndarray:
    """任意音频文件 -> float32 mono 16k（借助 ffmpeg）。"""
    import imageio_ffmpeg

    exe = imageio_ffmpeg.get_ffmpeg_exe()
    proc = await asyncio.create_subprocess_exec(
        exe,
        "-i",
        str(path),
        "-f",
        "s16le",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-vn",
        "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 解码失败: {stderr.decode(errors='ignore')[-500:]}")
    return np.frombuffer(stdout, dtype=np.int16).astype(np.float32) / 32768.0


def _batch_segments(
    audio: np.ndarray, vad_model
) -> list[tuple[float, float]]:
    """整段音频离线切分，返回 [(start_sec, end_sec)]。"""
    if vad_model is not None:
        from silero_vad import get_speech_timestamps

        ts = get_speech_timestamps(
            audio,
            vad_model,
            sampling_rate=16000,
            min_speech_duration_ms=250,
            min_silence_duration_ms=400,
            speech_pad_ms=120,
            max_speech_duration_s=int(MAX_SEGMENT_SECONDS),
        )
        return [(d["start"] / 16000, d["end"] / 16000) for d in ts]
    # 无 VAD 模型：固定步长切片
    step = int(MAX_SEGMENT_SECONDS * 16000)
    return [(float(i), float(min(i + step, len(audio))) / 16000) for i in range(0, len(audio), step)]


async def import_audio(
    path: Path,
    course: str,
    cfg: AppConfig,
    llm: LLMClient | None,
    asr: ASREngine,
    console=None,
) -> Path:
    """导入音频文件并生成归档，返回会话目录。"""
    start = datetime.now()
    safe_course = safe_name(course)
    session_dir = (
        cfg.archive_root
        / safe_course
        / start.strftime("%Y-%m-%d")
        / (start.strftime("%H%M%S") + "-import")
    )
    session_dir.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        if console:
            console.print(msg)
        else:
            logger.info(msg)

    log(f"解码音频: {path.name}")
    audio = await decode_audio(path)
    duration = len(audio) / 16000
    log(f"时长 {duration / 60:.1f} 分钟，开始切分语音段...")

    # 离线 VAD（同步加载模型即可）
    vad_model = None
    try:
        from silero_vad import load_silero_vad

        vad_model = load_silero_vad()
    except Exception:  # noqa: BLE001
        logger.warning("silero-vad 加载失败，使用固定切片", exc_info=True)

    regions = await asyncio.to_thread(_batch_segments, audio, vad_model)
    log(f"共 {len(regions)} 个语音段，开始转写...")

    store = TranscriptStore(session_dir / "transcript.jsonl", start)
    notes = NoteWriter(session_dir, store, llm, cfg.archive)
    for i, (t0, t1) in enumerate(regions, 1):
        samples = audio[int(t0 * 16000) : int(t1 * 16000)]
        try:
            text = await asyncio.to_thread(asr.transcribe, samples, cfg.asr.language)
        except Exception:  # noqa: BLE001
            logger.exception("转写失败（段 %d）", i)
            continue
        if text:
            segment = TranscriptSegment(t0=t0, t1=t1, text=text)
            wall = store.add(segment)
            notes.count_chars(text)
            log(f"  [{i}/{len(regions)}] [{wall}] {text}")
    store.close()

    log("生成笔记与课后总结...")
    await notes.maybe_summarize(force=True)
    summary = await notes.final_summary(course)
    if summary:
        log(f"完成: {summary}")
    return session_dir


def safe_name(name: str) -> str:
    bad = '<>:"/\\|?*'
    return "".join("_" if c in bad else c for c in name).strip() or "未命名课程"
