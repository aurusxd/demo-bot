from typing import Any, BinaryIO
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetFile

from app.clients.telegram import TelegramFileDownloader, TelegramLeadNotifier
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


def make_notifier_bot(fail_on: str | None = None) -> MagicMock:
    bot = MagicMock()
    for method in ("send_message", "send_photo", "send_media_group"):
        error = TelegramBadRequest(GetFile(file_id="x"), "bad") if method == fail_on else None
        setattr(bot, method, AsyncMock(side_effect=error))
    return bot


async def test_notifier_sends_card_as_html_without_photos() -> None:
    bot = make_notifier_bot()

    await TelegramLeadNotifier(bot, chat_id=777).notify("<b>Лид</b>", [])

    bot.send_message.assert_awaited_once_with(777, "<b>Лид</b>", parse_mode=ParseMode.HTML)
    bot.send_photo.assert_not_awaited()
    bot.send_media_group.assert_not_awaited()


async def test_notifier_sends_single_photo() -> None:
    bot = make_notifier_bot()

    await TelegramLeadNotifier(bot, chat_id=777).notify("card", ["p1"])

    bot.send_photo.assert_awaited_once_with(777, "p1")


async def test_notifier_sends_album_capped_at_ten() -> None:
    bot = make_notifier_bot()

    await TelegramLeadNotifier(bot, chat_id=777).notify("card", [f"p{i}" for i in range(12)])

    media = bot.send_media_group.await_args.args[1]
    assert [m.media for m in media] == [f"p{i}" for i in range(10)]


async def test_notifier_api_error_becomes_external_service_error() -> None:
    bot = make_notifier_bot(fail_on="send_message")

    with pytest.raises(ExternalServiceError):
        await TelegramLeadNotifier(bot, chat_id=777).notify("card", ["p1"])

    bot.send_photo.assert_not_awaited()
