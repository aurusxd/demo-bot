import contextlib
import html
from collections.abc import Awaitable, Callable

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardMarkup,
    InputFile,
    InputMediaPhoto,
    Message,
)
from loguru import logger

from app.bot import texts
from app.bot.keyboards import CATALOG_NOOP, CatalogPage, catalog_keyboard
from app.clients.telegram import TELEGRAM_ERRORS
from app.db.models import CatalogCategory
from app.services.catalog import CatalogCard, CatalogService

# Sends or edits one card with the given photo (cached file_id or a file upload).
PhotoSender = Callable[[str | InputFile], Awaitable[Message | bool]]
_NOT_MODIFIED = "message is not modified"
# A card can fail on the Bot API or on reading the image from disk (OSError).
CARD_ERRORS = (*TELEGRAM_ERRORS, OSError)


async def show_catalog(
    message: Message, catalog: CatalogService, category: CatalogCategory
) -> None:
    """First card of a category as a new message: the entry point from the dialog.

    Failures are logged and reported to the client, never raised: the dialog turn goes on
    to deliver the lead after this card.
    """
    card = await catalog.get_card(category, 0)
    if card is None:
        await message.answer(texts.CATALOG_EMPTY)
        return

    async def send(photo: str | InputFile) -> Message:
        return await message.answer_photo(
            photo, caption=_caption(card), reply_markup=_keyboard(card)
        )

    try:
        await _deliver(card, catalog, send)
    except CARD_ERRORS:
        logger.exception("catalog card failed item_id={}", card.item_id)
        with contextlib.suppress(*TELEGRAM_ERRORS):
            await message.answer(texts.CATALOG_FAILED)


async def catalog_page(
    callback: CallbackQuery, callback_data: CatalogPage, catalog: CatalogService
) -> None:
    """◀ ▶ and category buttons: the card is edited in place, the chat is not flooded."""
    card = await catalog.get_card(callback_data.category, callback_data.index)
    if card is None:
        await callback.answer()
        return
    message = callback.message
    bot = callback.bot
    assert bot is not None  # always bound for incoming updates

    async def edit(photo: str | InputFile) -> Message | bool:
        assert isinstance(message, Message)
        media = InputMediaPhoto(media=photo, caption=_caption(card))
        return await message.edit_media(media, reply_markup=_keyboard(card))

    async def send(photo: str | InputFile) -> Message:
        # The card message is no longer accessible (deleted or too old): send a new one.
        return await bot.send_photo(
            callback.from_user.id, photo, caption=_caption(card), reply_markup=_keyboard(card)
        )

    await _deliver(card, catalog, edit if isinstance(message, Message) else send)
    # Answered only on success: on failure the error handler answers with an alert instead.
    await callback.answer()


async def catalog_noop(callback: CallbackQuery) -> None:
    await callback.answer()


async def _deliver(card: CatalogCard, catalog: CatalogService, send: PhotoSender) -> None:
    """Send by the cached file_id; upload from disk if there is none or Telegram rejects it.

    A rejected file_id happens after the bot token changes: file ids are per bot.
    """
    if card.file_id is not None:
        try:
            await send(card.file_id)
            return
        except TelegramBadRequest as exc:
            if _NOT_MODIFIED in exc.message:  # double tap: the card already shows this
                return
            logger.warning(
                "catalog cached file_id rejected item_id={} error={}", card.item_id, exc.message
            )
    sent = await send(FSInputFile(card.image_path))
    if isinstance(sent, Message) and sent.photo:
        await catalog.remember_file_id(card.item_id, sent.photo[-1].file_id)


def _caption(card: CatalogCard) -> str:
    caption = texts.CATALOG_CAPTION.format(title=html.escape(card.title))
    if card.style:
        caption += texts.CATALOG_CAPTION_STYLE.format(style=html.escape(card.style))
    return caption


def _keyboard(card: CatalogCard) -> InlineKeyboardMarkup:
    return catalog_keyboard(card.category, card.index, card.total)


def create_router() -> Router:
    router = Router(name="catalog")
    router.callback_query.register(catalog_page, CatalogPage.filter())
    router.callback_query.register(catalog_noop, F.data == CATALOG_NOOP)
    return router
