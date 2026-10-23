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
from app.db.models import Lead, LeadStatus, User
from app.db.repositories import UserRepository
from tests.fakes import (
    BOT_TOKEN,
    FakeCrmClient,
    FakeLLMClient,
    FakeNotifier,
    llm_json,
    make_dispatcher,
    sent_texts,
    text_update,
)

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
    update.callback_query = None
    event = ErrorEvent.model_construct(update=update, exception=RuntimeError("boom"))

    assert await on_error(event) is True
    update.message.answer.assert_awaited_once_with(texts.ERROR_GENERIC)


def test_dispatcher_builds() -> None:
    session_factory = create_session_factory(create_engine("sqlite+aiosqlite://"))
    dp = make_dispatcher(session_factory, FakeLLMClient())

    assert [r.name for r in dp.sub_routers] == [
        "errors",
        "tryon",
        "start",
        "catalog",
        "media",
        "qualify",
    ]


async def test_start_update_flows_through_dispatcher(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    dp = make_dispatcher(session_factory, FakeLLMClient())

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
        llm_json(
            "Спасибо!",
            ready=True,
            dimensions="3 м, прямая",
            length_m=3,
            phone="+7 912 345 12 34",
        ),
    )
    crm, notifier = FakeCrmClient(), FakeNotifier()
    dp = make_dispatcher(session_factory, llm, crm=crm, notifier=notifier)
    bot = Bot(token=BOT_TOKEN)

    await dp.feed_update(bot, text_update(1, "/start"))
    await dp.feed_update(bot, text_update(2, "Анна"))
    await dp.feed_update(bot, text_update(3, "Хочу кухню <b>"))
    await dp.feed_update(bot, text_update(4, "3 метра прямая, 89123451234"))

    replies = sent_texts(sent)
    estimate = texts.ESTIMATE_RANGE.format(
        min_price=texts.format_price(115000, "₽"), max_price=texts.format_price(155000, "₽")
    )
    # No robotic summary: the LLM recaps itself, then only the estimate follows.
    assert replies == [
        texts.GREETING,
        "Приятно познакомиться, Анна! Что хотите заказать?",
        "Какие размеры кухни?",
        "Спасибо!",
        estimate,
    ]
    llm_replies = [m for m in sent if isinstance(m, SendMessage) and m.text != texts.GREETING]
    assert all(m.parse_mode is None for m in llm_replies[:-1])
    assert all(m.reply_markup is None for m in llm_replies)  # no buttons in the dialog
    # The greeting is part of the history sent to the LLM.
    assert json.loads(llm.calls[0][2].content)["reply"] == texts.GREETING
    assert [lead.phone for lead in crm.sent] == ["+79123451234"]
    assert len(notifier.cards) == 1
    async with session_factory() as session:
        lead = (await session.scalars(select(Lead))).one()
        assert lead.status is LeadStatus.SENT
        assert (lead.estimate_min, lead.estimate_max) == (115000, 155000)
        assert lead.sent_at is not None


async def test_llm_failure_answers_fallback(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    llm = FakeLLMClient(ExternalServiceError("deepseek", "timeout"))
    dp = make_dispatcher(session_factory, llm)

    await dp.feed_update(Bot(token=BOT_TOKEN), text_update(1, "Нужна гардеробная"))

    assert sent_texts(sent) == [texts.QUALIFY_FALLBACK]


async def test_too_long_text_is_rejected(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    llm = FakeLLMClient()
    dp = make_dispatcher(session_factory, llm)

    await dp.feed_update(Bot(token=BOT_TOKEN), text_update(1, "a" * 1001))

    assert sent_texts(sent) == [texts.TEXT_TOO_LONG]
    assert llm.calls == []


async def test_delete_me_erases_the_user_and_the_dialog_starts_over(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    llm = FakeLLMClient(llm_json("Как вас зовут?"), llm_json("Здравствуйте!"))
    dp = make_dispatcher(session_factory, llm)
    bot = Bot(token=BOT_TOKEN)

    await dp.feed_update(bot, text_update(1, "/start"))
    await dp.feed_update(bot, text_update(2, "Хочу кухню, телефон 89123451234"))
    await dp.feed_update(bot, text_update(3, "/delete_me"))

    assert sent_texts(sent)[-1] == texts.DELETE_ME_DONE
    async with session_factory() as session:
        assert (await session.scalars(select(User))).all() == []
        assert (await session.scalars(select(Lead))).all() == []

    await dp.feed_update(bot, text_update(4, "Привет"))

    # A fresh user and lead: the LLM sees nothing of the erased dialog.
    history = [m.content for m in llm.calls[1]]
    assert not any("89123451234" in content for content in history)
    async with session_factory() as session:
        assert len((await session.scalars(select(Lead))).all()) == 1
