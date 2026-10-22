from unittest.mock import AsyncMock, MagicMock

from aiogram.types import ErrorEvent

from app.bot import texts
from app.bot.handlers.errors import on_error
from app.bot.handlers.start import cmd_start
from app.config import Settings
from app.db.base import create_engine, create_session_factory
from app.main import build_dispatcher


async def test_start_replies_with_stub() -> None:
    message = AsyncMock()

    await cmd_start(message)

    message.answer.assert_awaited_once_with(texts.START_STUB)


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
