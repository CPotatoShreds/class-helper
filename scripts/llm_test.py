"""直连 LLM 验证：分级 JSON 与流式速答（UTF-8 文件方式避免 shell 编码干扰）。"""

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from class_helper.brain.detector import DETECT_SYSTEM
from class_helper.brain.llm import LLMClient
from class_helper.config import load_config
from class_helper.protocol import DetectionResult


async def main() -> None:
    cfg = load_config("config.toml")
    llm = LLMClient(cfg.llm)

    r = await llm.classify(
        DETECT_SYSTEM,
        "学生姓名/别名单：陈嘉毅\n\n"
        "老师最近讲话转写：\n"
        "[0s-4s] 下面我抽一位同学来回答一下这个问题\n"
        "[4s-8s] 陈嘉毅你来说说TCP三次握手",
    )
    res = DetectionResult.from_json(r)
    print("分级:", res.level, "| 问题:", res.question[:40], "| 理由:", res.reason[:40])

    print("--- 流式速答 ---")
    buf: list[str] = []
    async for d in llm.stream_answer(
        "你是课堂应答助手。最多5条要点，每条一行，以-开头。",
        "老师的问题：什么是TCP三次握手？",
    ):
        buf.append(d)
    ans = "".join(buf)
    print(ans[:300])
    leak = "<think>" in ans or "</think>" in ans
    print("---", "think泄漏!" if leak else "无think泄漏", "| 总字数", len(ans))


if __name__ == "__main__":
    asyncio.run(main())
