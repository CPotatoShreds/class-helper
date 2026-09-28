import struct

import pytest

from class_helper.protocol import (
    DetectionResult,
    decode_msg,
    encode_msg,
    iter_lines,
    pack_audio,
    unpack_audio,
)


def test_audio_pack_roundtrip():
    pcm = b"\x01\x02\x03\x04"
    frame = pack_audio(7, pcm)
    seq, out = unpack_audio(frame)
    assert seq == 7
    assert out == pcm
    assert unpack_audio(pack_audio(0xFFFFFFFF, b""))[0] == 0xFFFFFFFF
    assert struct.calcsize("<I") == 4


def test_unpack_too_short():
    with pytest.raises(ValueError):
        unpack_audio(b"\x01")


def test_msg_roundtrip():
    raw = encode_msg("transcript", t0=1.5, text="你好")
    data = decode_msg(raw)
    assert data["type"] == "transcript"
    assert data["text"] == "你好"


def test_decode_rejects_non_dict():
    import pytest as _pytest

    for bad in ["[]", '"x"', "123"]:
        with _pytest.raises(ValueError):
            decode_msg(bad)


def test_detection_result_json_extraction():
    noisy = "好的，判断如下：\n{\"level\": \"called\", \"question\": \"什么是TCP?\", \"addressed_to\": \"陈嘉毅\", \"quote\": \"陈嘉毅你来回答\", \"reason\": \"点名\"}\n以上"
    r = DetectionResult.from_json(noisy)
    assert r.level == "called"
    assert r.question == "什么是TCP?"


def test_detection_result_invalid_level():
    r = DetectionResult.from_json('{"level": "shouting"}')
    assert r.level == "none"


def test_detection_result_no_json():
    with pytest.raises(ValueError):
        DetectionResult.from_json("没有 JSON")


def test_iter_lines_strips_bullets():
    lines = list(iter_lines("- a\n* b\n• c\n\n普通行\n"))
    assert lines == ["a", "b", "c", "普通行"]
