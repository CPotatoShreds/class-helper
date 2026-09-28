"""速答器：根据老师的问题与课堂上下文，流式生成可口头作答的要点。"""

from __future__ import annotations

from collections.abc import AsyncIterator

from class_helper.brain.llm import LLMClient

ANSWER_SYSTEM = """你是大学生的课堂应答助手。老师刚刚点名提问，必须立刻给出可以口头回答的内容。
要求：
- 直接回答问题本身：先给结论，再给最关键的支撑
- 最多5条要点，每条一行，以"- "开头，总长不超过150字
- 优先依据课堂上下文（老师刚讲过的内容）作答；上下文没有的用你自己的知识
- 只输出要点，不要任何客套话、过渡语或"根据上下文"之类的元话语"""


class Responder:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def answer_stream(
        self, question: str, context_text: str
    ) -> AsyncIterator[str]:
        user_parts = []
        if context_text.strip():
            user_parts.append("课堂上下文（老师最近讲的内容，旧→新）：\n" + context_text.strip())
        user_parts.append("老师的问题：\n" + (question.strip() or "（转写未捕捉到明确问题，请根据课堂上下文推测老师可能问什么并作答）"))
        return self._llm.stream_answer(ANSWER_SYSTEM, "\n\n".join(user_parts))
