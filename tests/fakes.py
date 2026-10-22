import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser

from app.clients.base import ChatMessage
from app.config import Settings

BOT_TOKEN = "123456:" + "A" * 35


def make_settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, bot_token="123:abc", deepseek_api_key="k", **overrides)  # pyright: ignore[reportCallIssue]


def text_update(update_id: int, text: str, user_id: int = 500) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=datetime.now(UTC),
            chat=Chat(id=user_id, type="private"),
            from_user=TgUser(id=user_id, is_bot=False, first_name="Ann", username="ann"),
            text=text,
        ),
    )


def sent_texts(sent: list[TelegramMethod[Any]]) -> list[str]:
    return [m.text for m in sent if isinstance(m, SendMessage)]


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


class SlowLLMClient:
    """LLMClient that answers after `delay_s` and records the history it was given."""

    def __init__(self, delay_s: float) -> None:
        self._delay_s = delay_s
        self.calls: list[list[ChatMessage]] = []

    async def complete_json(self, messages: Sequence[ChatMessage]) -> str:
        self.calls.append(list(messages))
        await asyncio.sleep(self._delay_s)
        return llm_json(f"ответ {len(self.calls)}")

    async def close(self) -> None:
        return None
