import io
import time
from collections.abc import Generator, Sequence
from contextlib import contextmanager

import aiohttp  # aiogram's own HTTP backend: raised by Bot.download on transport errors
from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InputMediaPhoto
from loguru import logger

from app.core.errors import ExternalServiceError

SERVICE = "telegram"
DOWNLOAD_TIMEOUT_S = 30
MAX_ALBUM_SIZE = 10  # Telegram limit for sendMediaGroup
# Everything a Bot API call can raise on API or transport failure.
TELEGRAM_ERRORS = (TelegramAPIError, aiohttp.ClientError, TimeoutError)


@contextmanager
def _telegram_call(action: str) -> Generator[None]:
    """One log line per Bot API call; transport/API errors become ExternalServiceError."""
    started = time.perf_counter()
    try:
        yield
    except TELEGRAM_ERRORS as exc:
        # The exception text may contain a file URL with the bot token: log the type only.
        logger.warning(
            "telegram call action={} failed error={} latency_ms={}",
            action,
            type(exc).__name__,
            _elapsed_ms(started),
        )
        raise ExternalServiceError(SERVICE, f"{action} failed: {type(exc).__name__}") from exc
    logger.info("telegram call action={} latency_ms={}", action, _elapsed_ms(started))


class TelegramFileDownloader:
    """FileDownloader over the Bot API (getFile + file download)."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def download(self, file_id: str) -> bytes:
        buffer = io.BytesIO()
        with _telegram_call("download"):
            await self._bot.download(file_id, destination=buffer, timeout=DOWNLOAD_TIMEOUT_S)
        data = buffer.getvalue()
        logger.info("file downloaded service={} bytes={}", SERVICE, len(data))
        return data


class TelegramLeadNotifier:
    """LeadNotifier: HTML card to the managers' chat, then the lead's photos."""

    def __init__(self, bot: Bot, chat_id: int) -> None:
        self._bot = bot
        self._chat_id = chat_id

    async def notify(self, card_html: str, photo_file_ids: Sequence[str]) -> None:
        with _telegram_call("send_lead_card"):
            await self._bot.send_message(self._chat_id, card_html, parse_mode=ParseMode.HTML)
        photos = list(photo_file_ids)[:MAX_ALBUM_SIZE]
        if len(photos) == 1:
            with _telegram_call("send_lead_photo"):
                await self._bot.send_photo(self._chat_id, photos[0])
        elif photos:
            with _telegram_call("send_lead_album"):
                await self._bot.send_media_group(
                    self._chat_id, [InputMediaPhoto(media=file_id) for file_id in photos]
                )

    async def notify_photo(self, caption_html: str, file_id: str) -> None:
        with _telegram_call("send_lead_photo"):
            await self._bot.send_photo(
                self._chat_id, file_id, caption=caption_html, parse_mode=ParseMode.HTML
            )


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
