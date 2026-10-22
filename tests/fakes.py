import asyncio
import io
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiogram import Dispatcher
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import Chat, Document, Message, PhotoSize, Update, Voice
from aiogram.types import User as TgUser
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.clients.base import (
    ChatMessage,
    CrmClient,
    FileDownloader,
    LeadNotifier,
    LLMClient,
    SpeechClient,
)
from app.config import Settings
from app.core.errors import ExternalServiceError
from app.main import build_dispatcher
from app.schemas.lead import LeadPayload
from app.services.estimate import EstimateService, load_pricing
from app.services.leads import LeadServiceFactory

BOT_TOKEN = "123456:" + "A" * 35


def make_settings(**overrides: Any) -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        bot_token="123:abc",
        deepseek_api_key="k",
        polza_api_key="pk",
        polza_stt_model="openai/whisper-1",
        admin_chat_id=777,
        **overrides,
    )


def make_dispatcher(
    session_factory: async_sessionmaker[AsyncSession],
    llm: LLMClient,
    *,
    settings: Settings | None = None,
    speech: SpeechClient | None = None,
    downloader: FileDownloader | None = None,
    crm: CrmClient | None = None,
    notifier: LeadNotifier | None = None,
) -> Dispatcher:
    return build_dispatcher(
        settings or make_settings(),
        session_factory,
        llm,
        speech or FakeSpeechClient(),
        downloader or FakeDownloader(),
        make_lead_services(crm or FakeCrmClient(), notifier or FakeNotifier()),
    )


def make_lead_services(crm: CrmClient, notifier: LeadNotifier) -> LeadServiceFactory:
    return LeadServiceFactory(
        EstimateService(load_pricing(Path("config/pricing.yaml"))), crm, notifier
    )


def message_update(update_id: int, user_id: int = 500, **fields: Any) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=datetime.now(UTC),
            chat=Chat(id=user_id, type="private"),
            from_user=TgUser(id=user_id, is_bot=False, first_name="Ann", username="ann"),
            **fields,
        ),
    )


def text_update(update_id: int, text: str, user_id: int = 500) -> Update:
    return message_update(update_id, user_id, text=text)


def voice_update(update_id: int, file_id: str, duration: int = 5) -> Update:
    voice = Voice(file_id=file_id, file_unique_id=file_id, duration=duration, mime_type="audio/ogg")
    return message_update(update_id, voice=voice)


def photo_update(
    update_id: int, file_id: str, caption: str | None = None, media_group_id: str | None = None
) -> Update:
    sizes = [
        PhotoSize(file_id=f"{file_id}-thumb", file_unique_id="t", width=90, height=90),
        PhotoSize(file_id=file_id, file_unique_id=file_id, width=1280, height=960),
    ]
    return message_update(update_id, photo=sizes, caption=caption, media_group_id=media_group_id)


def document_update(update_id: int, mime_type: str) -> Update:
    document = Document(file_id="doc", file_unique_id="doc", mime_type=mime_type)
    return message_update(update_id, document=document)


def sent_texts(sent: list[TelegramMethod[Any]]) -> list[str]:
    return [m.text for m in sent if isinstance(m, SendMessage)]


def llm_json(reply: str = "Ок", ready: bool = False, **slots: object) -> str:
    return json.dumps({"reply": reply, "slots": slots, "ready": ready}, ensure_ascii=False)


class FakeLLMClient:
    """LLMClient that replays scripted responses (str) or raises scripted exceptions."""

    def __init__(self, *responses: str | Exception) -> None:
        self._responses = list(responses)
        self.calls: list[list[ChatMessage]] = []

    async def complete_json(self, messages: Sequence[ChatMessage]) -> str:
        self.calls.append(list(messages))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        return None


class FakeSpeechClient:
    """SpeechClient that replays scripted transcripts (str) or raises scripted exceptions."""

    def __init__(self, *responses: str | Exception) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[bytes, str]] = []

    async def transcribe(self, audio: bytes, mime_type: str) -> str:
        self.calls.append((audio, mime_type))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        return None


class FakeDownloader:
    """FileDownloader serving in-memory files by file_id; a missing id raises like Telegram."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self.files = dict(files or {})
        self.calls: list[str] = []

    async def download(self, file_id: str) -> bytes:
        self.calls.append(file_id)
        if file_id not in self.files:
            raise ExternalServiceError("telegram", "download failed")
        return self.files[file_id]


class FakeCrmClient:
    """CrmClient that records sent leads or raises a scripted error."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.sent: list[LeadPayload] = []

    async def send(self, lead: LeadPayload) -> None:
        if self._error is not None:
            raise self._error
        self.sent.append(lead)

    async def close(self) -> None:
        return None


class FakeNotifier:
    """LeadNotifier that records cards or raises a scripted error."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.cards: list[tuple[str, list[str]]] = []
        self.photos: list[tuple[str, str]] = []

    async def notify(self, card_html: str, photo_file_ids: Sequence[str]) -> None:
        if self._error is not None:
            raise self._error
        self.cards.append((card_html, list(photo_file_ids)))

    async def notify_photo(self, caption_html: str, file_id: str) -> None:
        if self._error is not None:
            raise self._error
        self.photos.append((caption_html, file_id))


def image_bytes(image_format: str = "JPEG", size: tuple[int, int] = (8, 8)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format=image_format)
    return buffer.getvalue()


class SlowLLMClient:
    """LLMClient that answers after `delay_s` and records the history it was given."""

    def __init__(self, delay_s: float) -> None:
        self._delay_s = delay_s
        self.calls: list[list[ChatMessage]] = []

    async def complete_json(self, messages: Sequence[ChatMessage]) -> str:
        self.calls.append(list(messages))
        await asyncio.sleep(self._delay_s)
        return llm_json(f"ответ {len(self.calls)}")

    async def close(self) -> None:
        return None
