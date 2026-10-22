import json
from collections.abc import Sequence

from app.clients.base import ChatMessage


def llm_json(reply: str = "Ок", ready: bool = False, **slots: object) -> str:
    return json.dumps({"reply": reply, "slots": slots, "ready": ready}, ensure_ascii=False)


class FakeLLMClient:
    """LLMClient that replays scripted responses (str) or raises scripted exceptions."""

    def __init__(self, *responses: str | Exception) -> None:
        self._responses = list(responses)
        self.calls: list[list[ChatMessage]] = []

    async def complete_json(self, messages: Sequence[ChatMessage]) -> str:
        self.calls.append(list(messages))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        return None
