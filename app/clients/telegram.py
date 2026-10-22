import io
import time

import aiohttp  # aiogram's own HTTP backend: raised by Bot.download on transport errors
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from loguru import logger

from app.core.errors import ExternalServiceError

SERVICE = "telegram"
DOWNLOAD_TIMEOUT_S = 30


class TelegramFileDownloader:
    """FileDownloader over the Bot API (getFile + file download), one log line per call."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def download(self, file_id: str) -> bytes:
        started = time.perf_counter()
        buffer = io.BytesIO()
        try:
            await self._bot.download(file_id, destination=buffer, timeout=DOWNLOAD_TIMEOUT_S)
        except (TelegramAPIError, aiohttp.ClientError, TimeoutError) as exc:
            # The exception text may contain the file URL with the bot token: log the type only.
            logger.warning(
                "file download service={} failed error={} latency_ms={}",
                SERVICE,
                type(exc).__name__,
                _elapsed_ms(started),
            )
            raise ExternalServiceError(SERVICE, f"download failed: {type(exc).__name__}") from exc
        data = buffer.getvalue()
        logger.info(
            "file download service={} bytes={} latency_ms={}",
            SERVICE,
            len(data),
            _elapsed_ms(started),
        )
        return data


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
