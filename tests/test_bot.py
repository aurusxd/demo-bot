import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import Chat, ErrorEvent, Message, ReplyKeyboardMarkup, Update
from aiogram.types import User as TgUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import keyboards, texts
from app.bot.handlers.errors import on_error
from app.bot.handlers.start import cmd_start, menu_coming_soon
from app.config import Settings
from app.core.errors import ExternalServiceError
from app.db.base import Base, create_engine, create_session_factory
from app.db.models import Lead, LeadStatus
from app.db.repositories import UserRepository
from app.main import build_dispatcher
from tests.fakes import FakeLLMClient, llm_json

TELEGRAM_DESCRIPTION_LIMIT = 512
TELEGRAM_SHORT_DESCRIPTION_LIMIT = 120


def utf16_len(text: str) -> int:
    # Telegram counts text length in UTF-16 code units (emoji = 2).
    return len(text.encode("utf-16-le")) // 2


def test_profile_texts_fit_telegram_limits() -> None:
    assert 0 < utf16_len(texts.BOT_DESCRIPTION) <= TELEGRAM_DESCRIPTION_LIMIT
    assert 0 < utf16_len(texts.BOT_SHORT_DESCRIPTION) <= TELEGRAM_SHORT_DESCRIPTION_LIMIT


def test_main_menu_has_three_buttons() -> None:
    labels = [button.text for row in keyboards.main_menu().keyboard for button in row]

    assert labels == [texts.BTN_LEAD, texts.BTN_CATALOG, texts.BTN_TRYON]


async def test_start_replies_with_menu() -> None:
    message = AsyncMock()

    await cmd_start(message)

    message.answer.assert_awaited_once()
    assert message.answer.await_args.args == (texts.START,)
    assert isinstance(message.answer.await_args.kwargs["reply_markup"], ReplyKeyboardMarkup)


async def test_menu_buttons_reply_coming_soon() -> None:
    message = AsyncMock()

    await menu_coming_soon(message)

    assert message.answer.await_args.args == (texts.COMING_SOON,)


async def test_error_handler_replies_generic_message() -> None:
    update = MagicMock()
    update.message = AsyncMock()
    event = ErrorEvent.model_construct(update=update, exception=RuntimeError("boom"))

    assert await on_error(event) is True
    update.message.answer.assert_awaited_once_with(texts.ERROR_GENERIC)


def make_settings() -> Settings:
    return Settings(_env_file=None, bot_token="123:abc", deepseek_api_key="k")  # pyright: ignore[reportCallIssue]


def test_dispatcher_builds() -> None:
    session_factory = create_session_factory(create_engine("sqlite+aiosqlite://"))
    dp = build_dispatcher(make_settings(), session_factory, FakeLLMClient())

    assert [r.name for r in dp.sub_routers] == ["errors", "start", "qualify"]


def text_update(update_id: int, text: str) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=datetime.now(UTC),
            chat=Chat(id=500, type="private"),
            from_user=TgUser(id=500, is_bot=False, first_name="Ann", username="ann"),
            text=text,
        ),
    )


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[TelegramMethod[Any]]:
    """Captures every Bot API call instead of sending it."""
    calls: list[TelegramMethod[Any]] = []

    async def fake_call(self: Bot, method: TelegramMethod[Any], request_timeout: int | None = None):
        calls.append(method)
        return True

    monkeypatch.setattr(Bot, "__call__", fake_call)
    return calls


def sent_texts(sent: list[TelegramMethod[Any]]) -> list[str]:
    return [m.text for m in sent if isinstance(m, SendMessage)]


BOT_TOKEN = "123456:" + "A" * 35


async def test_start_update_flows_through_dispatcher(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    dp = build_dispatcher(make_settings(), session_factory, FakeLLMClient())

    await dp.feed_update(Bot(token=BOT_TOKEN), text_update(1, "/start"))

    assert sent_texts(sent) == [texts.START]
    async with session_factory() as session:
        user = await UserRepository(session).upsert(500, "ann")
        assert user.id == 1  # created by UserMiddleware, not by this upsert


async def test_qualification_dialog_through_dispatcher(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    llm = FakeLLMClient(
        llm_json("Какие размеры кухни?", furniture_type="kitchen"),
        llm_json("Спасибо!", ready=True, dimensions="3 м, угловая", phone="+7 912 345 12 34"),
    )
    dp = build_dispatcher(make_settings(), session_factory, llm)
    bot = Bot(token=BOT_TOKEN)

    await dp.feed_update(bot, text_update(1, texts.BTN_LEAD))
    await dp.feed_update(bot, text_update(2, "Хочу кухню <b>"))
    await dp.feed_update(bot, text_update(3, "3 метра угловая, 89123451234"))

    replies = sent_texts(sent)
    assert replies[:3] == [texts.QUALIFY_START, "Какие размеры кухни?", "Спасибо!"]
    assert "• Мебель: кухня" in replies[3]
    assert "• Телефон: +79123451234" in replies[3]
    llm_replies = [m for m in sent if isinstance(m, SendMessage) and m.text != texts.QUALIFY_START]
    assert all(m.parse_mode is None for m in llm_replies)
    # The greeting is part of the history sent to the LLM.
    assert json.loads(llm.calls[0][2].content) == {"reply": texts.QUALIFY_START}
    async with session_factory() as session:
        lead = (await session.scalars(select(Lead))).one()
        assert lead.status is LeadStatus.QUALIFIED


async def test_llm_failure_answers_fallback(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    llm = FakeLLMClient(ExternalServiceError("deepseek", "timeout"))
    dp = build_dispatcher(make_settings(), session_factory, llm)

    await dp.feed_update(Bot(token=BOT_TOKEN), text_update(1, "Нужна гардеробная"))

    assert sent_texts(sent) == [texts.QUALIFY_FALLBACK]


async def test_too_long_text_is_rejected(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    llm = FakeLLMClient()
    dp = build_dispatcher(make_settings(), session_factory, llm)

    await dp.feed_update(Bot(token=BOT_TOKEN), text_update(1, "a" * 1001))

    assert sent_texts(sent) == [texts.TEXT_TOO_LONG]
    assert llm.calls == []
