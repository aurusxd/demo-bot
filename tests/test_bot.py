from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import Chat, ErrorEvent, Message, ReplyKeyboardMarkup, Update
from aiogram.types import User as TgUser

from app.bot import keyboards, texts
from app.bot.handlers.errors import on_error
from app.bot.handlers.start import cmd_start, menu_coming_soon
from app.config import Settings
from app.db.base import Base, create_engine, create_session_factory
from app.db.repositories import UserRepository
from app.main import build_dispatcher

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


def test_dispatcher_builds() -> None:
    settings = Settings(_env_file=None, bot_token="123:abc")  # pyright: ignore[reportCallIssue]
    dp = build_dispatcher(settings, create_session_factory(create_engine("sqlite+aiosqlite://")))

    assert len(dp.sub_routers) == 2


async def test_start_update_flows_through_dispatcher(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = create_session_factory(engine)
    settings = Settings(_env_file=None, bot_token="123:abc")  # pyright: ignore[reportCallIssue]
    dp = build_dispatcher(settings, session_factory)

    sent: list[TelegramMethod[Any]] = []

    async def fake_call(self: Bot, method: TelegramMethod[Any], request_timeout: int | None = None):
        sent.append(method)
        return True

    monkeypatch.setattr(Bot, "__call__", fake_call)
    bot = Bot(token="123456:" + "A" * 35)
    update = Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=500, type="private"),
            from_user=TgUser(id=500, is_bot=False, first_name="Ann", username="ann"),
            text="/start",
        ),
    )

    await dp.feed_update(bot, update)

    assert len(sent) == 1
    assert isinstance(sent[0], SendMessage)
    assert sent[0].text == texts.START
    async with session_factory() as session:
        user = await UserRepository(session).upsert(500, "ann")
        assert user.id == 1  # created by UserMiddleware, not by this upsert
    await engine.dispose()
