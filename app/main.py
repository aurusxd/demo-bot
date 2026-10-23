import asyncio
import contextlib
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.handlers import catalog, errors, media, qualify, start, tryon
from app.bot.handlers.media import MediaGroupTracker
from app.bot.lead_retry import LeadDeliveryRetrier
from app.bot.middlewares.db_session import DbSessionMiddleware
from app.bot.middlewares.logging import HandlerLoggingMiddleware, UpdateLoggingMiddleware
from app.bot.middlewares.services import ServicesMiddleware
from app.bot.middlewares.throttle import ThrottlingMiddleware
from app.bot.middlewares.user import UserMiddleware
from app.bot.middlewares.user_lock import UserLockMiddleware
from app.clients.base import FileDownloader, ImageGenClient, LLMClient, SpeechClient
from app.clients.crm import LogCrmClient, WebhookCrmClient
from app.clients.deepseek import DeepSeekClient
from app.clients.polza import PolzaImageClient, PolzaSpeechClient
from app.clients.telegram import TelegramFileDownloader, TelegramLeadNotifier
from app.config import Settings, get_settings
from app.core.locks import UserLocks
from app.db.base import create_engine, create_session_factory
from app.db.repositories import CatalogRepository, UnitOfWork
from app.logging_setup import setup_logging, setup_startup_logging
from app.services.catalog import CatalogService, load_catalog
from app.services.estimate import EstimateService, load_pricing
from app.services.leads import LeadServiceFactory
from app.services.media import MediaService
from app.services.tryon import TryOnServiceFactory


def build_dispatcher(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    llm: LLMClient,
    speech: SpeechClient,
    image_gen: ImageGenClient,
    downloader: FileDownloader,
    lead_services: LeadServiceFactory,
    locks: UserLocks | None = None,
) -> Dispatcher:
    """Manual DI container: wires middlewares, routers and shared dependencies."""
    # media_groups: albums already acknowledged; tryon_albums: albums whose first photo
    # went to the try-on (the rest of such an album is skipped).
    dp = Dispatcher(
        settings=settings, media_groups=MediaGroupTracker(), tryon_albums=MediaGroupTracker()
    )
    # Order matters: throttle and serialize per user *before* opening a DB session.
    dp.update.outer_middleware(UpdateLoggingMiddleware())
    dp.update.outer_middleware(
        ThrottlingMiddleware(
            settings.throttle_max_messages,
            settings.throttle_window_s,
            settings.throttle_max_callbacks,
        )
    )
    dp.update.outer_middleware(UserLockMiddleware(locks or UserLocks()))
    dp.update.outer_middleware(DbSessionMiddleware(session_factory))
    dp.update.outer_middleware(UserMiddleware())
    catalog_dir = settings.catalog_path.parent
    tryon_services = TryOnServiceFactory(
        image_gen, catalog_dir, settings.tryon_daily_limit, frozenset(settings.admin_ids)
    )
    dp.update.outer_middleware(
        ServicesMiddleware(
            llm, MediaService(downloader, speech), lead_services, tryon_services, catalog_dir
        )
    )
    for observer in (dp.message, dp.callback_query):
        observer.middleware(HandlerLoggingMiddleware())

    # Order matters: try-on first (it drops its state on any non-photo message and passes
    # the message on), commands before the free-text dialog catch-all.
    dp.include_routers(
        errors.create_router(),
        tryon.create_router(),
        start.create_router(),
        catalog.create_router(),
        media.create_router(),
        qualify.create_router(),
    )
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
    speech = PolzaSpeechClient.create(
        settings.polza_base_url,
        settings.polza_api_key.get_secret_value(),
        settings.polza_stt_model,
        settings.polza_stt_provider,
    )
    image_gen = PolzaImageClient.create(
        settings.polza_base_url,
        settings.polza_api_key.get_secret_value(),
        settings.polza_image_model,
        settings.polza_image_provider,
    )
    pricing = await asyncio.to_thread(load_pricing, settings.pricing_path)
    crm = _create_crm(settings)
    session_factory = create_session_factory(engine)
    await _seed_catalog(settings, session_factory)
    locks = UserLocks()  # shared: handlers and the retry job never touch one user at once
    lead_services = LeadServiceFactory(
        EstimateService(pricing), crm, TelegramLeadNotifier(bot, settings.admin_chat_id)
    )
    dp = build_dispatcher(
        settings,
        session_factory,
        llm,
        speech,
        image_gen,
        TelegramFileDownloader(bot),
        lead_services,
        locks,
    )
    retrier = LeadDeliveryRetrier(
        session_factory, lead_services, locks, settings.lead_retry_interval_s
    )
    retry_task = asyncio.create_task(retrier.run_forever())
    logger.info("bot starting (polling)")
    try:
        # aiogram's own annotation of allowed_updates is partially unknown.
        await dp.start_polling(bot)  # pyright: ignore[reportUnknownMemberType]
    finally:
        retry_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await retry_task
        await bot.session.close()
        await llm.close()
        await speech.close()
        await image_gen.close()
        await crm.close()
        await engine.dispose()
        logger.info("bot stopped")


async def _seed_catalog(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    config = await asyncio.to_thread(load_catalog, settings.catalog_path)
    async with session_factory() as session:
        service = CatalogService(
            CatalogRepository(session), UnitOfWork(session), settings.catalog_path.parent
        )
        await service.seed(config)


def _create_crm(settings: Settings) -> LogCrmClient | WebhookCrmClient:
    if settings.crm_webhook_url is None:
        logger.info("crm client=log (CRM_WEBHOOK_URL is empty)")
        return LogCrmClient()
    logger.info("crm client=webhook")
    return WebhookCrmClient.create(settings.crm_webhook_url.get_secret_value())


def _exit_with_error(_: BaseException) -> None:
    sys.exit(1)


if __name__ == "__main__":
    # Fatal startup errors (bad .env, token, network) go through loguru, i.e. sanitized.
    setup_startup_logging()
    with logger.catch(onerror=_exit_with_error):
        asyncio.run(main())
