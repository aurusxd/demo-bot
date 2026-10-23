from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.bot import texts
from scripts.set_bot_profile import apply_profile


async def test_apply_profile_sets_descriptions_and_commands() -> None:
    bot = AsyncMock()

    await apply_profile(bot)

    bot.set_my_description.assert_awaited_once_with(description=texts.BOT_DESCRIPTION)
    bot.set_my_short_description.assert_awaited_once_with(
        short_description=texts.BOT_SHORT_DESCRIPTION
    )
    commands = bot.set_my_commands.await_args.args[0]
    assert [c.command for c in commands] == ["start", "delete_me"]


async def test_apply_profile_propagates_api_error() -> None:
    bot = AsyncMock()
    bot.set_my_description.side_effect = TelegramBadRequest(method=AsyncMock(), message="bad")

    with pytest.raises(TelegramBadRequest):
        await apply_profile(bot)

    bot.set_my_short_description.assert_not_awaited()
