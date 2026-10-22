import asyncio
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers import errors, qualify, start
from app.bot.middlewares.db_session import DbSessionMiddleware
from app.bot.middlewares.logging import HandlerLoggingMiddleware, UpdateLoggingMiddleware
from app.bot.middlewares.services import ServicesMiddleware
from app.bot.middlewares.throttle import ThrottlingMiddleware
from app.bot.middlewares.user import UserMiddleware
from app.bot.middlewares.user_lock import UserLockMiddleware
from app.clients.base import LLMClient
from app.clients.deepseek import DeepSeekClient
from app.config import Settings, get_settings
from app.db.base import create_engine, create_session_factory
from app.logging_setup import setup_logging


def build_dispatcher(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession], llm: LLMClient
) -> Dispatcher:
    """Manual DI container: wires middlewares, routers and shared dependencies."""
    dp = Dispatcher(settings=settings)
    # Order matters: throttle and serialize per user *before* opening a DB session.
    dp.update.outer_middleware(UpdateLoggingMiddleware())
    dp.update.outer_middleware(
        ThrottlingMiddleware(settings.throttle_max_messages, settings.throttle_window_s)
    )
    dp.update.outer_middleware(UserLockMiddleware())
    dp.update.outer_middleware(DbSessionMiddleware(session_factory))
    dp.update.outer_middleware(UserMiddleware())
    dp.update.outer_middleware(ServicesMiddleware(llm))
    for observer in (dp.message, dp.callback_query):
        observer.middleware(HandlerLoggingMiddleware())

    # Order matters: commands before the free-text dialog catch-all.
    dp.include_routers(errors.create_router(), start.create_router(), qualify.create_router())
    return dp


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level, settings.secret_values())

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    engine = create_engine(settings.database_url)
    llm = DeepSeekClient.create(
        settings.deepseek_base_url,
        settings.deepseek_api_key.get_secret_value(),
        settings.deepseek_model,
    )
    dp = build_dispatcher(settings, create_session_factory(engine), llm)
    logger.info("bot starting (polling)")
    try:
        # aiogram's own annotation of allowed_updates is partially unknown.
        await dp.start_polling(bot)  # pyright: ignore[reportUnknownMemberType]
    finally:
        await bot.session.close()
        await llm.close()
        await engine.dispose()
        logger.info("bot stopped")


def _exit_with_error(_: BaseException) -> None:
    sys.exit(1)


if __name__ == "__main__":
    # Fatal startup errors (bad token, network) go through loguru, i.e. sanitized.
    with logger.catch(onerror=_exit_with_error):
        asyncio.run(main())
