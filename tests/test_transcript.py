import json

import pytest

from class_helper.archive.transcript import TranscriptStore
from class_helper.protocol import TranscriptSegment


@pytest.fixture
def store(tmp_path):
    from datetime import datetime

    s = TranscriptStore(tmp_path / "transcript.jsonl", datetime(2026, 9, 28, 8, 0, 0))
    yield s
    s.close()


def test_add_persists_and_reads_back(store, tmp_path):
    store.add(TranscriptSegment(0, 2.5, "第一句话"))
    store.add(TranscriptSegment(60, 62, "第二句话"))
    lines = (tmp_path / "transcript.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    data = json.loads(lines[0])
    assert data["text"] == "第一句话"
    assert data["wall"] == "08:00"


def test_recent_window(store):
    store.add(TranscriptSegment(0, 5, "早期"))
    store.add(TranscriptSegment(100, 105, "最近1"))
    store.add(TranscriptSegment(110, 115, "最近2"))
    recent = store.recent(20)
    assert [s.text for s in recent] == ["最近1", "最近2"]


def test_since_and_context(store):
    store.add(TranscriptSegment(0, 5, "A"))
    store.add(TranscriptSegment(10, 15, "B"))
    assert [s.text for s in store.since(1)] == ["B"]
    assert "08:00" in store.context_text(store.segments)
    assert "B" in store.all_text()
