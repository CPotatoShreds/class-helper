import numpy as np
import pytest

from class_helper.audio.vad import StreamVAD


def test_energy_vad_detects_speech_segment():
    vad = StreamVAD(model=None)
    out = []
    out += vad.process(b"\x00\x00" * 160)  # 10ms 静音
    out += vad.process(np.repeat(np.int16(16000), 16000 * 2).tobytes())  # 2s 响音
    out += vad.process(b"\x00\x00" * 16000)  # 1s 静音触发 hangover
    assert len(out) == 1
    seg = out[0]
    assert seg.start >= 0
    assert seg.samples.dtype == np.float32
    # 时长约 2s 语音 + preroll(0.12s) + hangover 内的静音（~0.5s）
    assert 2.0 <= (seg.end - seg.start) <= 2.8


def test_silence_only_produces_no_segments():
    vad = StreamVAD(model=None)
    assert vad.process(b"\x00\x00" * 16000 * 3) == []


def test_remainder_carries_across_chunks():
    """不足一帧的样本要跨块保留，时长守恒。"""
    vad = StreamVAD(model=None)
    # 一次喂 1000 samples（1.953 帧），共 3 次，确保无样本丢失
    total = 0
    for _ in range(3):
        vad.process(np.repeat(np.int16(10000), 1000).tobytes())
        total += 1000
    assert vad._time == pytest.approx(total // 512 * 512 / 16000)


def test_flush_emits_trailing_speech():
    vad = StreamVAD(model=None)
    vad.process(np.repeat(np.int16(16000), 16000).tobytes())  # 1s 响音，仍在语音中
    seg = vad.flush()
    assert seg is not None
    assert seg.end - seg.start >= 1.0
