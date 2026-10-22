import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from aiogram import Bot
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import ErrorEvent, ReplyKeyboardRemove
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import texts
from app.bot.handlers.errors import on_error
from app.bot.handlers.start import cmd_start
from app.core.errors import ExternalServiceError
from app.db.base import create_engine, create_session_factory
from app.db.models import Lead, LeadStatus
from app.db.repositories import UserRepository
from app.main import build_dispatcher
from tests.fakes import BOT_TOKEN, FakeLLMClient, llm_json, make_settings, sent_texts, text_update

TELEGRAM_DESCRIPTION_LIMIT = 512
TELEGRAM_SHORT_DESCRIPTION_LIMIT = 120


def utf16_len(text: str) -> int:
    # Telegram counts text length in UTF-16 code units (emoji = 2).
    return len(text.encode("utf-16-le")) // 2


def test_profile_texts_fit_telegram_limits() -> None:
    assert 0 < utf16_len(texts.BOT_DESCRIPTION) <= TELEGRAM_DESCRIPTION_LIMIT
    assert 0 < utf16_len(texts.BOT_SHORT_DESCRIPTION) <= TELEGRAM_SHORT_DESCRIPTION_LIMIT


async def test_start_greets_starts_dialog_and_removes_old_keyboard() -> None:
    message = AsyncMock()
    user = MagicMock()
    qualification = AsyncMock()

    await cmd_start(message, user, qualification)

    qualification.start.assert_awaited_once_with(user, texts.GREETING)
    assert message.answer.await_args.args == (texts.GREETING,)
    assert isinstance(message.answer.await_args.kwargs["reply_markup"], ReplyKeyboardRemove)


async def test_error_handler_replies_generic_message() -> None:
    update = MagicMock()
    update.message = AsyncMock()
    event = ErrorEvent.model_construct(update=update, exception=RuntimeError("boom"))

    assert await on_error(event) is True
    update.message.answer.assert_awaited_once_with(texts.ERROR_GENERIC)


def test_dispatcher_builds() -> None:
    session_factory = create_session_factory(create_engine("sqlite+aiosqlite://"))
    dp = build_dispatcher(make_settings(), session_factory, FakeLLMClient())

    assert [r.name for r in dp.sub_routers] == ["errors", "start", "qualify"]


async def test_start_update_flows_through_dispatcher(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    dp = build_dispatcher(make_settings(), session_factory, FakeLLMClient())

    await dp.feed_update(Bot(token=BOT_TOKEN), text_update(1, "/start"))

    assert sent_texts(sent) == [texts.GREETING]
    async with session_factory() as session:
        user = await UserRepository(session).upsert(500, "ann")
        assert user.id == 1  # created by UserMiddleware, not by this upsert


async def test_qualification_dialog_through_dispatcher(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    llm = FakeLLMClient(
        llm_json("Приятно познакомиться, Анна! Что хотите заказать?", name="Анна"),
        llm_json("Какие размеры кухни?", furniture_type="kitchen"),
        llm_json("Спасибо!", ready=True, dimensions="3 м, угловая", phone="+7 912 345 12 34"),
    )
    dp = build_dispatcher(make_settings(), session_factory, llm)
    bot = Bot(token=BOT_TOKEN)

    await dp.feed_update(bot, text_update(1, "/start"))
    await dp.feed_update(bot, text_update(2, "Анна"))
    await dp.feed_update(bot, text_update(3, "Хочу кухню <b>"))
    await dp.feed_update(bot, text_update(4, "3 метра угловая, 89123451234"))

    replies = sent_texts(sent)
    assert replies[:4] == [
        texts.GREETING,
        "Приятно познакомиться, Анна! Что хотите заказать?",
        "Какие размеры кухни?",
        "Спасибо!",
    ]
    assert len(replies) == 4  # no extra summary message: the LLM recaps itself
    llm_replies = [m for m in sent if isinstance(m, SendMessage) and m.text != texts.GREETING]
    assert all(m.parse_mode is None for m in llm_replies)
    assert all(m.reply_markup is None for m in llm_replies)  # no buttons in the dialog
    # The greeting is part of the history sent to the LLM.
    assert json.loads(llm.calls[0][2].content)["reply"] == texts.GREETING
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
