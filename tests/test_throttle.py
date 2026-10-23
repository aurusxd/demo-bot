"""Review #5: per-user rate limit protects the LLM budget."""

from typing import Any
from unittest.mock import AsyncMock

from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, TelegramMethod
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import texts
from app.bot.middlewares.throttle import ThrottlingMiddleware
from tests.fakes import (
    BOT_TOKEN,
    FakeLLMClient,
    callback_update,
    llm_json,
    make_dispatcher,
    make_settings,
    sent_texts,
    text_update,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def user_data(user_id: int) -> dict[str, Any]:
    return {"event_from_user": TgUser(id=user_id, is_bot=False, first_name="x")}


async def test_limit_window_and_per_user_isolation() -> None:
    clock = FakeClock()
    throttle = ThrottlingMiddleware(max_updates=2, window_s=60, clock=clock)
    handler = AsyncMock(return_value="ok")
    event = AsyncMock()

    results = [await throttle(handler, event, user_data(1)) for _ in range(3)]
    other_user = await throttle(handler, event, user_data(2))
    clock.now = 61  # the window has passed
    after_window = await throttle(handler, event, user_data(1))

    assert results == ["ok", "ok", None]
    assert other_user == "ok"
    assert after_window == "ok"
    assert handler.await_count == 4


async def test_updates_without_user_are_not_throttled() -> None:
    throttle = ThrottlingMiddleware(max_updates=1, window_s=60)
    handler = AsyncMock(return_value="ok")

    results = [await throttle(handler, AsyncMock(), {}) for _ in range(3)]

    assert results == ["ok", "ok", "ok"]


async def test_flood_is_dropped_before_llm_with_a_single_warning(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    llm = FakeLLMClient(llm_json("ответ 1"), llm_json("ответ 2"))
    settings = make_settings(throttle_max_messages=2, throttle_window_s=60)
    dp = make_dispatcher(session_factory, llm, settings=settings)
    bot = Bot(token=BOT_TOKEN)

    for i in range(1, 6):
        await dp.feed_update(bot, text_update(i, f"сообщение {i}"))

    assert sent_texts(sent) == ["ответ 1", "ответ 2", texts.THROTTLED]
    assert len(llm.calls) == 2


async def test_button_presses_have_own_limit_and_are_always_answered(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    llm = FakeLLMClient(llm_json("ответ"))
    settings = make_settings(throttle_max_messages=2, throttle_max_callbacks=3)
    dp = make_dispatcher(session_factory, llm, settings=settings)
    bot = Bot(token=BOT_TOKEN)

    for i in range(1, 6):
        await dp.feed_update(bot, callback_update(i, "cat_noop"))
    await dp.feed_update(bot, text_update(10, "сообщение"))

    answers = [m for m in sent if isinstance(m, AnswerCallbackQuery)]
    # 3 presses handled (noop answer), 2 over the limit answered with the warning.
    assert [a.text for a in answers] == [None, None, None, texts.THROTTLED, texts.THROTTLED]
    # Paging did not eat the dialog's budget: the message still reaches the LLM.
    assert sent_texts(sent) == ["ответ"]
    assert len(llm.calls) == 1
