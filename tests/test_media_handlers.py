from typing import Any

from aiogram import Bot
from aiogram.methods import SendMessage, TelegramMethod
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import texts
from app.bot.handlers.media import MediaGroupTracker
from app.core.errors import ExternalServiceError
from app.db.models import LeadMedia, Message, MessageKind, MessageRole
from app.prompts.qualification import PHOTO_MARKER
from app.schemas.lead import LeadEvent
from app.services.media import MAX_VOICE_DURATION_S
from tests.fakes import (
    BOT_TOKEN,
    FakeCrmClient,
    FakeDownloader,
    FakeLLMClient,
    FakeNotifier,
    FakeSpeechClient,
    document_update,
    image_bytes,
    llm_json,
    make_dispatcher,
    photo_update,
    sent_texts,
    text_update,
    voice_update,
)

VOICE = b"OggS-voice"


async def get_messages(factory: async_sessionmaker[AsyncSession]) -> list[Message]:
    async with factory() as session:
        return list((await session.scalars(select(Message).order_by(Message.id))).all())


async def get_media_ids(factory: async_sessionmaker[AsyncSession]) -> list[str]:
    async with factory() as session:
        media = await session.scalars(select(LeadMedia).order_by(LeadMedia.id))
        return [m.tg_file_id for m in media]


async def test_voice_goes_through_qualification(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    llm = FakeLLMClient(llm_json("Какие размеры кухни?", furniture_type="kitchen"))
    speech = FakeSpeechClient("Хочу кухню")
    dp = make_dispatcher(
        session_factory, llm, speech=speech, downloader=FakeDownloader({"v1": VOICE})
    )

    await dp.feed_update(Bot(token=BOT_TOKEN), voice_update(1, "v1"))

    assert sent_texts(sent) == ["Какие размеры кухни?"]
    assert [m.parse_mode for m in sent if isinstance(m, SendMessage)] == [None]
    assert llm.calls[0][-1].content == "Хочу кухню"
    user_messages = [m for m in await get_messages(session_factory) if m.role is MessageRole.USER]
    assert [(m.content, m.kind) for m in user_messages] == [("Хочу кухню", MessageKind.VOICE)]


async def test_too_long_voice_is_rejected_without_api_calls(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    downloader = FakeDownloader({"v1": VOICE})
    llm = FakeLLMClient()
    dp = make_dispatcher(session_factory, llm, downloader=downloader)

    await dp.feed_update(
        Bot(token=BOT_TOKEN), voice_update(1, "v1", duration=MAX_VOICE_DURATION_S + 1)
    )

    assert sent_texts(sent) == [texts.VOICE_TOO_LONG]
    assert downloader.calls == []
    assert llm.calls == []


async def test_stt_failure_answers_voice_failed(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    speech = FakeSpeechClient(ExternalServiceError("polza", "HTTP 502", 502))
    dp = make_dispatcher(
        session_factory, FakeLLMClient(), speech=speech, downloader=FakeDownloader({"v1": VOICE})
    )

    await dp.feed_update(Bot(token=BOT_TOKEN), voice_update(1, "v1"))

    assert sent_texts(sent) == [texts.VOICE_FAILED]


async def test_silent_voice_asks_to_repeat(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    dp = make_dispatcher(
        session_factory,
        FakeLLMClient(),
        speech=FakeSpeechClient(""),
        downloader=FakeDownloader({"v1": VOICE}),
    )

    await dp.feed_update(Bot(token=BOT_TOKEN), voice_update(1, "v1"))

    assert sent_texts(sent) == [texts.VOICE_NOT_RECOGNIZED]


async def test_photo_is_attached_and_acknowledged(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    llm = FakeLLMClient()
    dp = make_dispatcher(session_factory, llm, downloader=FakeDownloader({"p1": image_bytes()}))

    await dp.feed_update(Bot(token=BOT_TOKEN), photo_update(1, "p1"))

    assert sent_texts(sent) == [texts.PHOTO_RECEIVED]
    assert await get_media_ids(session_factory) == ["p1"]  # the largest size is kept
    assert llm.calls == []


async def test_album_is_acknowledged_once(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    files = {"p1": image_bytes(), "p2": image_bytes("PNG")}
    dp = make_dispatcher(session_factory, FakeLLMClient(), downloader=FakeDownloader(files))
    bot = Bot(token=BOT_TOKEN)

    await dp.feed_update(bot, photo_update(1, "p1", media_group_id="album"))
    await dp.feed_update(bot, photo_update(2, "p2", media_group_id="album"))

    assert sent_texts(sent) == [texts.PHOTO_RECEIVED]
    assert await get_media_ids(session_factory) == ["p1", "p2"]


async def test_photo_caption_is_a_dialog_turn(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    llm = FakeLLMClient(llm_json("Красивая кухня! Какие размеры?"))
    dp = make_dispatcher(session_factory, llm, downloader=FakeDownloader({"p1": image_bytes()}))

    await dp.feed_update(Bot(token=BOT_TOKEN), photo_update(1, "p1", caption="Хочу такую"))

    assert sent_texts(sent) == ["Красивая кухня! Какие размеры?"]
    history = [m.content for m in llm.calls[0] if m.role == "user"]
    assert history == [PHOTO_MARKER, "Хочу такую"]


async def test_invalid_photo_is_rejected(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    dp = make_dispatcher(
        session_factory, FakeLLMClient(), downloader=FakeDownloader({"p1": b"broken"})
    )

    await dp.feed_update(Bot(token=BOT_TOKEN), photo_update(1, "p1"))

    assert sent_texts(sent) == [texts.PHOTO_INVALID]
    assert await get_media_ids(session_factory) == []


async def test_photo_download_failure_answers_photo_failed(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    dp = make_dispatcher(session_factory, FakeLLMClient(), downloader=FakeDownloader())

    await dp.feed_update(Bot(token=BOT_TOKEN), photo_update(1, "p1"))

    assert sent_texts(sent) == [texts.PHOTO_FAILED]


async def test_image_file_asks_to_send_as_photo(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    dp = make_dispatcher(session_factory, FakeLLMClient())

    await dp.feed_update(Bot(token=BOT_TOKEN), document_update(1, "image/jpeg"))
    await dp.feed_update(Bot(token=BOT_TOKEN), document_update(2, "application/pdf"))

    assert sent_texts(sent) == [texts.SEND_AS_PHOTO]  # non-image files stay unhandled


def test_media_group_tracker_forgets_oldest() -> None:
    tracker = MediaGroupTracker(max_size=2)

    assert [tracker.first_seen(g) for g in ("a", "a", "b", "c", "a")] == [
        True,
        False,
        True,
        True,
        True,  # "a" was evicted
    ]
    assert tracker.first_seen(None) is True


async def test_additions_after_delivery_reach_managers(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    """Review #1: a city and a photo sent after the card are forwarded as additions."""
    llm = FakeLLMClient(
        llm_json(
            "Спасибо!",
            ready=True,
            furniture_type="kitchen",
            dimensions="3 м",
            length_m=3,
            phone="89123451234",
        ),
        llm_json("Записал город", city="Казань"),
    )
    crm, notifier = FakeCrmClient(), FakeNotifier()
    dp = make_dispatcher(
        session_factory,
        llm,
        downloader=FakeDownloader({"late": image_bytes()}),
        crm=crm,
        notifier=notifier,
    )
    bot = Bot(token=BOT_TOKEN)

    await dp.feed_update(bot, text_update(1, "Кухня 3 м, 89123451234"))
    await dp.feed_update(bot, text_update(2, "Я из Казани"))
    await dp.feed_update(bot, photo_update(3, "late"))

    assert [lead.event for lead in crm.sent] == [
        LeadEvent.CREATED,
        LeadEvent.UPDATED,
        LeadEvent.UPDATED,
    ]
    assert len(notifier.cards) == 2
    assert "Казань" in notifier.cards[1][0]
    assert [file_id for _, file_id in notifier.photos] == ["late"]
    assert sent_texts(sent)[-1] == texts.PHOTO_RECEIVED  # the client sees the usual answer


async def test_photo_before_delivery_is_not_forwarded_twice(
    session_factory: async_sessionmaker[AsyncSession], sent: list[TelegramMethod[Any]]
) -> None:
    """A captioned photo that completes qualification is already in the first card."""
    llm = FakeLLMClient(
        llm_json(
            "Спасибо!", ready=True, furniture_type="kitchen", dimensions="3 м", phone="89123451234"
        )
    )
    notifier = FakeNotifier()
    dp = make_dispatcher(
        session_factory, llm, downloader=FakeDownloader({"p1": image_bytes()}), notifier=notifier
    )

    await dp.feed_update(Bot(token=BOT_TOKEN), photo_update(1, "p1", caption="Кухня, 89123451234"))

    assert [photos for _, photos in notifier.cards] == [["p1"]]
    assert notifier.photos == []
