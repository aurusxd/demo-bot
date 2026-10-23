import contextlib
import html

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.chat_action import ChatActionSender
from loguru import logger

from app.bot import texts
from app.bot.handlers.catalog import show_catalog
from app.bot.handlers.qualify import run_dialog_turn
from app.bot.keyboards import TryOnLead, TryOnMore, TryOnStart, tryon_result_keyboard
from app.bot.states import TryOn
from app.clients.telegram import TELEGRAM_ERRORS
from app.config import Settings
from app.core.errors import (
    CatalogItemNotFoundError,
    ExternalServiceError,
    InvalidInputError,
    TryOnFailedError,
    TryOnLimitError,
)
from app.db.models import User
from app.services.catalog import CatalogService
from app.services.leads import LeadService
from app.services.media import MediaService, PhotoInput
from app.services.qualification import QualificationService
from app.services.tryon import TryOnResult, TryOnService

RESULT_FILENAME = "tryon.jpg"
ITEM_ID_KEY = "item_id"


async def tryon_start(
    callback: CallbackQuery,
    callback_data: TryOnStart,
    state: FSMContext,
    user: User,
    tryon: TryOnService,
    settings: Settings,
) -> None:
    """«Попробовать у себя»: check the item and the limit, then wait for a room photo."""
    try:
        item = await tryon.get_item(callback_data.item_id)
        tryon.ensure_quota(user)
    except CatalogItemNotFoundError:
        await callback.answer(texts.TRYON_ITEM_GONE, show_alert=True)
        return
    except TryOnLimitError:
        await callback.answer(
            texts.TRYON_LIMIT.format(limit=settings.tryon_daily_limit), show_alert=True
        )
        return
    await state.set_state(TryOn.waiting_photo)
    await state.update_data({ITEM_ID_KEY: item.item_id})
    logger.info("fsm state=tryon.waiting_photo item_id={}", item.item_id)
    await callback.answer()
    bot = callback.bot
    assert bot is not None  # always bound for incoming updates
    await bot.send_message(
        callback.from_user.id, texts.TRYON_ASK_PHOTO.format(title=html.escape(item.title))
    )


async def tryon_photo(
    message: Message,
    state: FSMContext,
    user: User,
    media: MediaService,
    tryon: TryOnService,
    settings: Settings,
) -> None:
    """The room photo: generate and send the try-on. On failure the state stays: resend."""
    assert message.photo  # guaranteed by the F.photo filter
    photo = message.photo[-1]  # the largest size
    item_id = int((await state.get_data())[ITEM_ID_KEY])
    try:
        room = await media.prepare_tryon_photo(PhotoInput(photo.file_id, photo.file_size))
    except InvalidInputError:
        await message.answer(texts.PHOTO_INVALID)
        return
    except ExternalServiceError as exc:
        logger.warning("tryon photo failed service={} status={}", exc.service, exc.status_code)
        await message.answer(texts.PHOTO_FAILED)
        return

    try:
        result = await _generate(message, user, tryon, item_id, room)
    except TryOnFailedError:
        await message.answer(texts.TRYON_FAILED)
        return
    except TryOnLimitError:
        await _clear(state)
        await message.answer(texts.TRYON_LIMIT.format(limit=settings.tryon_daily_limit))
        return
    except CatalogItemNotFoundError:
        await _clear(state)
        await message.answer(texts.TRYON_ITEM_GONE)
        return

    await _clear(state)
    await message.answer_photo(
        BufferedInputFile(result.image, RESULT_FILENAME),
        caption=texts.TRYON_RESULT_CAPTION.format(title=html.escape(result.item.title)),
        reply_markup=tryon_result_keyboard(result.item.category, result.item.item_id),
    )


async def tryon_cancel(message: Message, state: FSMContext) -> None:
    """Anything but a photo while waiting for one: the client moved on, so does the bot."""
    await _clear(state)
    raise SkipHandler  # the message is handled as usual by the next routers


async def tryon_more(
    callback: CallbackQuery, callback_data: TryOnMore, catalog: CatalogService
) -> None:
    """«Другая мебель»: a new catalog card; the try-on result above stays in the chat."""
    await callback.answer()
    if isinstance(callback.message, Message):
        await show_catalog(callback.message, catalog, callback_data.category)


async def tryon_lead(
    callback: CallbackQuery,
    callback_data: TryOnLead,
    user: User,
    tryon: TryOnService,
    qualification: QualificationService,
    leads: LeadService,
    catalog: CatalogService,
) -> None:
    """«Оставить заявку»: a dialog turn as if the client wrote it; the buttons go away."""
    await callback.answer()
    message = callback.message
    if not isinstance(message, Message):
        return
    # One press — one LLM turn: the button cannot be spammed.
    with contextlib.suppress(*TELEGRAM_ERRORS):
        await message.edit_reply_markup(reply_markup=None)
    try:
        item = await tryon.get_item(callback_data.item_id)
    except CatalogItemNotFoundError:
        await message.answer(texts.TRYON_ITEM_GONE)
        return
    text = texts.TRYON_LEAD_REQUEST.format(title=item.title)
    await run_dialog_turn(message, user, qualification, leads, catalog, text)


async def _generate(
    message: Message, user: User, tryon: TryOnService, item_id: int, room: bytes
) -> TryOnResult:
    """«⏳ Генерирую…» while the image API works; the message is removed afterwards."""
    bot = message.bot
    assert bot is not None  # always bound for incoming updates
    progress = await message.answer(texts.TRYON_GENERATING)
    try:
        async with ChatActionSender.upload_photo(bot=bot, chat_id=message.chat.id):
            return await tryon.generate(user, item_id, room)
    finally:
        with contextlib.suppress(*TELEGRAM_ERRORS):
            await bot.delete_message(message.chat.id, progress.message_id)


async def _clear(state: FSMContext) -> None:
    await state.clear()
    logger.info("fsm state cleared (tryon)")


def create_router() -> Router:
    router = Router(name="tryon")
    router.callback_query.register(tryon_start, TryOnStart.filter())
    router.callback_query.register(tryon_more, TryOnMore.filter())
    router.callback_query.register(tryon_lead, TryOnLead.filter())
    router.message.register(tryon_photo, TryOn.waiting_photo, F.photo)
    router.message.register(tryon_cancel, TryOn.waiting_photo)
    return router
