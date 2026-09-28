from class_helper.brain.detector import Detector
from class_helper.brain.responder import Responder
from class_helper.protocol import TranscriptSegment
from conftest import FakeLLM


def test_prefilter_alias_hit(cfg):
    d = Detector(cfg.detection, FakeLLM())
    hit, alias = d.prefilter_hit("好的，陈嘉毅你来说说这道题")
    assert hit and alias == "陈嘉毅"


def test_prefilter_trigger_hit(cfg):
    d = Detector(cfg.detection, FakeLLM())
    hit, alias = d.prefilter_hit("下面我抽一位同学来回答")
    assert hit and alias is None


def test_prefilter_miss(cfg):
    d = Detector(cfg.detection, FakeLLM())
    hit, alias = d.prefilter_hit("今天我们讲第三章第二节")
    assert not hit and alias is None


async def test_classify_parses_level(cfg):
    fake = FakeLLM(classify_result={"level": "preparing", "reason": "老师宣布抽人"})
    d = Detector(cfg.detection, fake)
    segs = [TranscriptSegment(0, 2, "我抽一位同学回答")]
    result = await d.classify(segs)
    assert result.level == "preparing"


def test_responder_builds_context(cfg):
    fake = FakeLLM()
    r = Responder(fake)
    stream = r.answer_stream("什么是TCP?", "[12:00] 讲了三次握手")
    assert stream is not None
