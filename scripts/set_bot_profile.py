"""Sets bot description, short description and command list via Bot API.

Usage: make profile. The description picture is uploaded manually via @BotFather
(/setdescriptionpic), the file lives in assets/header/.
"""

import asyncio

from aiogram import Bot
from aiogram.types import BotCommand
from loguru import logger

from app.bot import texts
from app.bot.handlers.start import DELETE_ME_COMMAND
from app.config import get_settings
from app.logging_setup import setup_logging


async def apply_profile(bot: Bot) -> None:
    await bot.set_my_description(description=texts.BOT_DESCRIPTION)
    await bot.set_my_short_description(short_description=texts.BOT_SHORT_DESCRIPTION)
    await bot.set_my_commands(
        [
            BotCommand(command="start", description=texts.COMMAND_START),
            BotCommand(command=DELETE_ME_COMMAND, description=texts.COMMAND_DELETE_ME),
        ]
    )
    logger.info(
        "bot profile updated description_len={} short_len={}",
        len(texts.BOT_DESCRIPTION),
        len(texts.BOT_SHORT_DESCRIPTION),
    )


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, settings.secret_values(), log_file=None)
    bot = Bot(token=settings.bot_token.get_secret_value())
    try:
        await apply_profile(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    with logger.catch(reraise=True):
        asyncio.run(main())
