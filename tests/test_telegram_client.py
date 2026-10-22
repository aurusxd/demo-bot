from typing import Any, BinaryIO
from unittest.mock import MagicMock

import aiohttp
import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetFile

from app.clients.telegram import TelegramFileDownloader
from app.core.errors import ExternalServiceError


def make_bot(result: bytes | Exception) -> MagicMock:
    async def download(file_id: str, destination: BinaryIO, **_: Any) -> BinaryIO:
        if isinstance(result, Exception):
            raise result
        destination.write(result)
        return destination

    bot = MagicMock()
    bot.download = download
    return bot


async def test_download_returns_bytes() -> None:
    downloader = TelegramFileDownloader(make_bot(b"voice-bytes"))

    assert await downloader.download("f1") == b"voice-bytes"


@pytest.mark.parametrize(
    "error",
    [
        TelegramBadRequest(GetFile(file_id="f1"), "file is too big"),
        aiohttp.ClientConnectionError("https://api.telegram.org/file/bot123:secret/x"),
        TimeoutError(),
    ],
)
async def test_download_errors_become_external_service_error(error: Exception) -> None:
    downloader = TelegramFileDownloader(make_bot(error))

    with pytest.raises(ExternalServiceError) as exc_info:
        await downloader.download("f1")

    assert exc_info.value.service == "telegram"
    assert "secret" not in str(exc_info.value)  # the file URL carries the bot token
