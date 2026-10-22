"""Review #1: concurrency on a real file-based SQLite (in-memory StaticPool hides locks)."""

import asyncio
import json
import time
from typing import Any

from aiogram import Bot
from aiogram.methods import TelegramMethod
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import texts
from app.db.models import Lead, Message
from tests.fakes import BOT_TOKEN, SlowLLMClient, make_dispatcher, sent_texts, text_update

LLM_DELAY_S = 1.0


async def timed_feed(dp: Any, bot: Bot, update: Any) -> float:
    started = time.perf_counter()
    await dp.feed_update(bot, update)
    return time.perf_counter() - started


async def test_other_users_are_not_blocked_while_llm_thinks(
    file_session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    dp = make_dispatcher(file_session_factory, SlowLLMClient(LLM_DELAY_S))
    bot = Bot(token=BOT_TOKEN)

    slow_a, fast_b = await asyncio.gather(
        timed_feed(dp, bot, text_update(1, "хочу кухню", user_id=100)),
        timed_feed(dp, bot, text_update(2, "/start", user_id=200)),
    )

    replies = sent_texts(sent)
    assert texts.ERROR_GENERIC not in replies
    assert texts.GREETING in replies and "ответ 1" in replies
    assert slow_a >= LLM_DELAY_S
    assert fast_b < LLM_DELAY_S / 2  # B did not wait for A's LLM call


async def test_one_users_messages_are_processed_in_order(
    file_session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    llm = SlowLLMClient(0.2)
    dp = make_dispatcher(file_session_factory, llm)
    bot = Bot(token=BOT_TOKEN)

    await asyncio.gather(
        dp.feed_update(bot, text_update(1, "первое", user_id=300)),
        dp.feed_update(bot, text_update(2, "второе", user_id=300)),
    )

    assert sent_texts(sent) == ["ответ 1", "ответ 2"]
    # The second turn saw the first one completely (user message + bot reply).
    second_history = [(m.role, m.content) for m in llm.calls[1][2:]]
    assert second_history[0] == ("user", "первое")
    assert json.loads(second_history[1][1])["reply"] == "ответ 1"
    assert second_history[2] == ("user", "второе")
    async with file_session_factory() as session:
        assert len((await session.scalars(select(Lead))).all()) == 1
        assert len((await session.scalars(select(Message))).all()) == 4
