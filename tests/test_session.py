"""会话全链路集成测试（假 ASR/假 LLM/能量 VAD）。"""

import asyncio

from class_helper.server.session import ClassSession
from conftest import EventCollector, FakeASR, FakeLLM, noise_pcm, silence_pcm


async def _wait_until(predicate, timeout=5.0, step=0.05):
    for _ in range(int(timeout / step)):
        if predicate():
            return True
        await asyncio.sleep(step)
    return False


async def _make_session(cfg, events, llm, asr, course="计算机网络") -> ClassSession:
    session = ClassSession(
        course=course,
        cfg=cfg,
        llm=llm,
        asr=asr,
        emit=events,
        vad_model=None,  # 能量检测兜底，测试无需 silero 模型
    )
    await session.run()
    return session


async def test_alias_hit_triggers_called_and_answer(cfg):
    events = EventCollector()
    llm = FakeLLM(classify_result={"level": "called", "question": "什么是TCP?"})
    asr = FakeASR(["老师讲第一章", "陈嘉毅你来回答一下"])
    session = await _make_session(cfg, events, llm, asr)

    session.push_audio(noise_pcm(2.0))
    session.push_audio(silence_pcm(1.0))
    session.push_audio(noise_pcm(2.0))
    session.push_audio(silence_pcm(1.0))

    assert await _wait_until(lambda: len(events.of_type("answer_done")) == 1)

    transcripts = events.of_type("transcript")
    assert len(transcripts) == 2
    alerts = events.of_type("alert")
    assert alerts and alerts[0]["level"] == "called"
    assert alerts[0]["addressed_to"] == "陈嘉毅"
    done = events.of_type("answer_done")[0]
    assert "要点一" in done["answer"]

    result = await session.stop()
    assert result["transcripts"] == 2
    # 归档产物
    assert (session.session_dir / "transcript.jsonl").read_text(encoding="utf-8").count("\n") == 2
    assert (session.session_dir / "qa.md").exists()
    assert (session.session_dir / "session_summary.md").exists()


async def test_trigger_word_goes_through_llm(cfg):
    events = EventCollector()
    llm = FakeLLM(classify_result={"level": "preparing", "reason": "宣布抽人", "quote": "抽一位"})
    asr = FakeASR(["下面我抽一位同学来回答"])
    session = await _make_session(cfg, events, llm, asr)

    session.push_audio(noise_pcm(2.0))
    session.push_audio(silence_pcm(1.0))

    assert await _wait_until(lambda: bool(events.of_type("alert")))
    alert = events.of_type("alert")[0]
    assert alert["level"] == "preparing"
    assert not events.of_type("answer_done")  # preparing 不触发速答
    await session.stop()


async def test_manual_alert_answers_immediately(cfg):
    events = EventCollector()
    llm = FakeLLM(classify_result={"level": "called", "question": "解释一下三次握手"})
    asr = FakeASR(["讲了一些内容"])
    session = await _make_session(cfg, events, llm, asr)

    session.push_audio(noise_pcm(2.0))
    session.push_audio(silence_pcm(1.0))
    assert await _wait_until(lambda: bool(events.of_type("transcript")))

    await session.manual_alert()
    assert await _wait_until(lambda: bool(events.of_type("answer_done")))
    assert events.of_type("alert")[0]["question"] == "解释一下三次握手"
    await session.stop()


async def test_stop_without_audio_writes_no_summary(cfg):
    events = EventCollector()
    session = await _make_session(cfg, events, FakeLLM(), FakeASR([]))
    result = await session.stop()
    assert result["transcripts"] == 0
    assert result["summary_path"] == ""


async def test_status_reports_progress(cfg):
    events = EventCollector()
    session = await _make_session(cfg, events, FakeLLM(), FakeASR(["内容"]))
    session.push_audio(noise_pcm(2.0))
    session.push_audio(silence_pcm(1.0))
    assert await _wait_until(lambda: bool(events.of_type("transcript")))
    status = session.status()
    assert status["course"] == "计算机网络"
    assert status["transcripts"] == 1
    assert status["elapsed_seconds"] >= 0
    await session.stop()
