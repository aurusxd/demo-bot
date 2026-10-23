import asyncio
import io
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from aiogram import Bot
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageReplyMarkup,
    SendMessage,
    SendPhoto,
    TelegramMethod,
)
from aiogram.types import BufferedInputFile, Chat, Message, PhotoSize
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import texts
from app.bot.keyboards import TryOnLead, TryOnMore, TryOnStart
from app.core.errors import (
    CatalogItemNotFoundError,
    ExternalServiceError,
    TryOnFailedError,
    TryOnLimitError,
)
from app.db.models import CatalogCategory, LeadMedia, TryOnRequest, TryOnStatus, User
from app.db.repositories import (
    CatalogRepository,
    TryOnRequestRepository,
    UnitOfWork,
    UserRepository,
)
from app.services.catalog import CatalogService, load_catalog
from app.services.tryon import TryOnService
from tests.fakes import (
    BOT_TOKEN,
    FakeDownloader,
    FakeImageGenClient,
    FakeLLMClient,
    callback_update,
    image_bytes,
    llm_json,
    make_dispatcher,
    make_settings,
    photo_update,
    text_update,
)
from tests.test_catalog import entry, write_catalog

TODAY = date(2026, 10, 23)
RESULT = b"result-jpeg"
ROOM = image_bytes("JPEG", size=(64, 48))


async def seed_catalog(session: AsyncSession, directory: Path) -> Path:
    """One kitchen and one interior on disk and in the DB; returns catalog.yaml."""
    path = write_catalog(
        directory,
        [
            entry("kitchen/1.jpg", title="Белая кухня"),
            entry("interior/1.jpg", "interior", "Сканди"),
        ],
    )
    await CatalogService(CatalogRepository(session), UnitOfWork(session), directory).seed(
        load_catalog(path)
    )
    return path


async def make_user(session: AsyncSession, tg_id: int = 500) -> User:
    user = await UserRepository(session).upsert(tg_id, "ann")
    await session.commit()
    return user


def make_service(
    session: AsyncSession,
    image_gen: FakeImageGenClient,
    images_dir: Path,
    *,
    daily_limit: int = 2,
    unlimited: frozenset[int] = frozenset(),
    budget_s: float = 5.0,
) -> TryOnService:
    return TryOnService(
        image_gen,
        UserRepository(session),
        CatalogRepository(session),
        TryOnRequestRepository(session),
        UnitOfWork(session),
        images_dir,
        daily_limit,
        unlimited,
        today=lambda: TODAY,
        budget_s=budget_s,
    )


async def get_requests(session: AsyncSession) -> list[TryOnRequest]:
    return list((await session.scalars(select(TryOnRequest).order_by(TryOnRequest.id))).all())


# --- service ---


async def test_generate_records_request_and_counts_usage(
    session: AsyncSession, tmp_path: Path
) -> None:
    await seed_catalog(session, tmp_path)
    user = await make_user(session)
    image_gen = FakeImageGenClient(RESULT)
    service = make_service(session, image_gen, tmp_path)

    result = await service.generate(user, 1, ROOM)

    assert result.image == RESULT
    assert (result.item.item_id, result.item.title) == (1, "Белая кухня")
    room, reference, prompt = image_gen.calls[0]
    assert room == ROOM
    with Image.open(io.BytesIO(reference)) as image:
        assert image.format == "JPEG"  # the catalog photo is normalized too
    assert "Белая кухня" in prompt and "furniture" in prompt
    (request,) = await get_requests(session)
    assert request.status is TryOnStatus.DONE
    assert request.latency_ms is not None and request.error is None
    assert (user.tryon_used_today, user.tryon_reset_date) == (1, TODAY)


async def test_interior_uses_restyle_prompt(session: AsyncSession, tmp_path: Path) -> None:
    await seed_catalog(session, tmp_path)
    image_gen = FakeImageGenClient(RESULT)

    await make_service(session, image_gen, tmp_path).generate(await make_user(session), 2, ROOM)

    assert "Redesign the room" in image_gen.calls[0][2]


async def test_daily_limit_blocks_and_resets_next_day(
    session: AsyncSession, tmp_path: Path
) -> None:
    await seed_catalog(session, tmp_path)
    user = await make_user(session)
    image_gen = FakeImageGenClient(RESULT, RESULT)
    service = make_service(session, image_gen, tmp_path, daily_limit=1)
    await service.generate(user, 1, ROOM)

    with pytest.raises(TryOnLimitError):
        service.ensure_quota(user)
    with pytest.raises(TryOnLimitError):
        await service.generate(user, 1, ROOM)
    assert len(await get_requests(session)) == 1

    user.tryon_reset_date = TODAY - timedelta(days=1)  # yesterday's usage does not count
    await service.generate(user, 1, ROOM)
    assert (user.tryon_used_today, user.tryon_reset_date) == (1, TODAY)


async def test_unlimited_users_are_not_counted(session: AsyncSession, tmp_path: Path) -> None:
    await seed_catalog(session, tmp_path)
    user = await make_user(session, tg_id=42)
    service = make_service(
        session,
        FakeImageGenClient(RESULT, RESULT),
        tmp_path,
        daily_limit=0,
        unlimited=frozenset({42}),
    )

    await service.generate(user, 1, ROOM)
    await service.generate(user, 1, ROOM)

    assert user.tryon_used_today == 0


async def test_api_error_marks_failed_and_refunds(session: AsyncSession, tmp_path: Path) -> None:
    await seed_catalog(session, tmp_path)
    user = await make_user(session)
    service = make_service(
        session, FakeImageGenClient(ExternalServiceError("polza", "HTTP 502", 502)), tmp_path
    )

    with pytest.raises(TryOnFailedError):
        await service.generate(user, 1, ROOM)

    (request,) = await get_requests(session)
    assert (request.status, request.error) == (TryOnStatus.FAILED, "polza: HTTP 502")
    assert user.tryon_used_today == 0


async def test_timeout_marks_failed(session: AsyncSession, tmp_path: Path) -> None:
    class SlowImageGen(FakeImageGenClient):
        async def edit(self, room_photo: bytes, reference_photo: bytes, prompt: str) -> bytes:
            await asyncio.sleep(1)
            return RESULT

    await seed_catalog(session, tmp_path)
    service = make_service(session, SlowImageGen(), tmp_path, budget_s=0.01)

    with pytest.raises(TryOnFailedError):
        await service.generate(await make_user(session), 1, ROOM)

    (request,) = await get_requests(session)
    assert (request.status, request.error) == (TryOnStatus.FAILED, "TimeoutError")


async def test_missing_reference_file_fails(session: AsyncSession, tmp_path: Path) -> None:
    await seed_catalog(session, tmp_path)
    (tmp_path / "kitchen" / "1.jpg").unlink()
    image_gen = FakeImageGenClient()

    with pytest.raises(TryOnFailedError):
        await make_service(session, image_gen, tmp_path).generate(await make_user(session), 1, ROOM)

    assert image_gen.calls == []


async def test_unknown_item_is_rejected(session: AsyncSession, tmp_path: Path) -> None:
    service = make_service(session, FakeImageGenClient(), tmp_path)

    with pytest.raises(CatalogItemNotFoundError):
        await service.get_item(99)
    with pytest.raises(CatalogItemNotFoundError):
        await service.generate(await make_user(session), 99, ROOM)
    assert await get_requests(session) == []


# --- handlers through the dispatcher ---


class FakeTelegram:
    """Bot API stand-in: sent messages and photos come back as Message objects."""

    def __init__(self) -> None:
        self.calls: list[TelegramMethod[Any]] = []

    async def handle(self, method: TelegramMethod[Any]) -> Any:
        self.calls.append(method)
        if isinstance(method, SendMessage | SendPhoto):
            photo = (
                [PhotoSize(file_id="sent", file_unique_id="sent", width=1, height=1)]
                if isinstance(method, SendPhoto)
                else None
            )
            return Message(
                message_id=len(self.calls),
                date=datetime.now(UTC),
                chat=Chat(id=500, type="private"),
                photo=photo,
            )
        return True

    def of(self, kind: type[TelegramMethod[Any]]) -> list[Any]:
        return [m for m in self.calls if isinstance(m, kind)]

    def texts(self) -> list[str]:
        return [m.text for m in self.of(SendMessage)]


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> FakeTelegram:
    fake = FakeTelegram()

    async def fake_call(self: Bot, method: TelegramMethod[Any], request_timeout: int | None = None):
        return await fake.handle(method)

    monkeypatch.setattr(Bot, "__call__", fake_call)
    return fake


@pytest.fixture
async def catalog_yaml(tmp_path: Path, session_factory: async_sessionmaker[AsyncSession]) -> Path:
    async with session_factory() as session:
        return await seed_catalog(session, tmp_path)


def press_tryon(update_id: int, item_id: int = 1) -> Any:
    return callback_update(update_id, TryOnStart(item_id=item_id).pack())


async def lead_photo_ids(factory: async_sessionmaker[AsyncSession]) -> list[str]:
    async with factory() as session:
        return list((await session.scalars(select(LeadMedia.tg_file_id))).all())


async def test_tryon_flow_sends_result_and_leaves_the_state(
    session_factory: async_sessionmaker[AsyncSession], catalog_yaml: Path, telegram: FakeTelegram
) -> None:
    image_gen = FakeImageGenClient(RESULT)
    dp = make_dispatcher(
        session_factory,
        FakeLLMClient(),
        settings=make_settings(catalog_path=catalog_yaml),
        image_gen=image_gen,
        downloader=FakeDownloader({"room": ROOM, "plan": image_bytes()}),
    )
    bot = Bot(token=BOT_TOKEN)

    await dp.feed_update(bot, press_tryon(1))
    await dp.feed_update(bot, photo_update(2, "room"))
    await dp.feed_update(bot, photo_update(3, "plan"))  # after the try-on: a lead photo again

    assert telegram.texts() == [
        texts.TRYON_ASK_PHOTO.format(title="Белая кухня"),
        texts.TRYON_GENERATING,
        texts.PHOTO_RECEIVED,
    ]
    (result,) = telegram.of(SendPhoto)
    assert isinstance(result.photo, BufferedInputFile) and result.photo.data == RESULT
    assert result.caption == texts.TRYON_RESULT_CAPTION.format(title="Белая кухня")
    assert [b.callback_data for b in result.reply_markup.inline_keyboard[0]] == [
        TryOnMore(category=CatalogCategory.KITCHEN).pack(),
        TryOnLead(item_id=1).pack(),
    ]
    assert len(telegram.of(DeleteMessage)) == 1  # the «Генерирую…» message is removed
    assert len(image_gen.calls) == 1
    assert await lead_photo_ids(session_factory) == ["plan"]  # the room photo is not kept


async def test_failed_generation_keeps_waiting_for_a_photo(
    session_factory: async_sessionmaker[AsyncSession], catalog_yaml: Path, telegram: FakeTelegram
) -> None:
    image_gen = FakeImageGenClient(ExternalServiceError("polza", "HTTP 502", 502), RESULT)
    dp = make_dispatcher(
        session_factory,
        FakeLLMClient(),
        settings=make_settings(catalog_path=catalog_yaml),
        image_gen=image_gen,
        downloader=FakeDownloader({"room": ROOM}),
    )
    bot = Bot(token=BOT_TOKEN)

    await dp.feed_update(bot, press_tryon(1))
    await dp.feed_update(bot, photo_update(2, "room"))
    await dp.feed_update(bot, photo_update(3, "room"))  # resent: tried again

    assert texts.TRYON_FAILED in telegram.texts()
    assert len(telegram.of(SendPhoto)) == 1
    assert len(image_gen.calls) == 2


async def test_invalid_room_photo_is_rejected_without_generation(
    session_factory: async_sessionmaker[AsyncSession], catalog_yaml: Path, telegram: FakeTelegram
) -> None:
    image_gen = FakeImageGenClient()
    dp = make_dispatcher(
        session_factory,
        FakeLLMClient(),
        settings=make_settings(catalog_path=catalog_yaml),
        image_gen=image_gen,
        downloader=FakeDownloader({"room": b"not an image"}),
    )
    bot = Bot(token=BOT_TOKEN)

    await dp.feed_update(bot, press_tryon(1))
    await dp.feed_update(bot, photo_update(2, "room"))

    assert telegram.texts()[-1] == texts.PHOTO_INVALID
    assert image_gen.calls == []


async def test_limit_is_checked_on_the_button(
    session_factory: async_sessionmaker[AsyncSession], catalog_yaml: Path, telegram: FakeTelegram
) -> None:
    dp = make_dispatcher(
        session_factory,
        FakeLLMClient(),
        settings=make_settings(catalog_path=catalog_yaml, tryon_daily_limit=0),
    )

    await dp.feed_update(Bot(token=BOT_TOKEN), press_tryon(1))

    (answer,) = telegram.of(AnswerCallbackQuery)
    assert answer.text == texts.TRYON_LIMIT.format(limit=0)
    assert answer.show_alert is True
    assert telegram.texts() == []


async def test_forged_item_answers_alert(
    session_factory: async_sessionmaker[AsyncSession], catalog_yaml: Path, telegram: FakeTelegram
) -> None:
    dp = make_dispatcher(
        session_factory, FakeLLMClient(), settings=make_settings(catalog_path=catalog_yaml)
    )

    await dp.feed_update(Bot(token=BOT_TOKEN), press_tryon(1, item_id=999))

    (answer,) = telegram.of(AnswerCallbackQuery)
    assert answer.text == texts.TRYON_ITEM_GONE


async def test_text_while_waiting_cancels_tryon_and_goes_to_dialog(
    session_factory: async_sessionmaker[AsyncSession], catalog_yaml: Path, telegram: FakeTelegram
) -> None:
    llm = FakeLLMClient(llm_json("Расскажите о размерах"))
    image_gen = FakeImageGenClient()
    dp = make_dispatcher(
        session_factory,
        llm,
        settings=make_settings(catalog_path=catalog_yaml),
        image_gen=image_gen,
        downloader=FakeDownloader({"room": ROOM}),
    )
    bot = Bot(token=BOT_TOKEN)

    await dp.feed_update(bot, press_tryon(1))
    await dp.feed_update(bot, text_update(2, "А сколько стоит?"))
    await dp.feed_update(bot, photo_update(3, "room"))

    assert telegram.texts()[1:] == ["Расскажите о размерах", texts.PHOTO_RECEIVED]
    assert image_gen.calls == []
    assert await lead_photo_ids(session_factory) == ["room"]


async def test_lead_button_continues_the_dialog_once(
    session_factory: async_sessionmaker[AsyncSession], catalog_yaml: Path, telegram: FakeTelegram
) -> None:
    llm = FakeLLMClient(llm_json("Отлично! Как вас зовут?"))
    dp = make_dispatcher(session_factory, llm, settings=make_settings(catalog_path=catalog_yaml))

    await dp.feed_update(Bot(token=BOT_TOKEN), callback_update(1, TryOnLead(item_id=1).pack()))

    assert llm.calls[0][-1].content == texts.TRYON_LEAD_REQUEST.format(title="Белая кухня")
    assert telegram.texts() == ["Отлично! Как вас зовут?"]
    (edit,) = telegram.of(EditMessageReplyMarkup)
    assert edit.reply_markup is None


async def test_more_button_sends_a_new_catalog_card(
    session_factory: async_sessionmaker[AsyncSession], catalog_yaml: Path, telegram: FakeTelegram
) -> None:
    dp = make_dispatcher(
        session_factory, FakeLLMClient(), settings=make_settings(catalog_path=catalog_yaml)
    )
    data = TryOnMore(category=CatalogCategory.INTERIOR).pack()

    await dp.feed_update(Bot(token=BOT_TOKEN), callback_update(1, data))

    (card,) = telegram.of(SendPhoto)
    assert card.caption.startswith("<b>Сканди</b>")
    assert card.reply_markup.inline_keyboard[0][0].callback_data == TryOnStart(item_id=2).pack()


async def test_album_uses_only_its_first_photo(
    session_factory: async_sessionmaker[AsyncSession], catalog_yaml: Path, telegram: FakeTelegram
) -> None:
    image_gen = FakeImageGenClient(RESULT)
    dp = make_dispatcher(
        session_factory,
        FakeLLMClient(),
        settings=make_settings(catalog_path=catalog_yaml),
        image_gen=image_gen,
        downloader=FakeDownloader({"a": ROOM, "b": ROOM, "c": ROOM, "next": image_bytes()}),
    )
    bot = Bot(token=BOT_TOKEN)

    await dp.feed_update(bot, press_tryon(1))
    for update_id, file_id in enumerate(("a", "b", "c"), start=2):
        await dp.feed_update(bot, photo_update(update_id, file_id, media_group_id="album"))
    await dp.feed_update(bot, photo_update(9, "next", media_group_id="other"))

    assert len(image_gen.calls) == 1  # one paid generation per album
    assert len(telegram.of(SendPhoto)) == 1
    # The rest of the try-on album is neither acknowledged nor attached to the lead;
    # a later album is a lead photo again.
    assert telegram.texts().count(texts.PHOTO_RECEIVED) == 1
    assert await lead_photo_ids(session_factory) == ["next"]


async def test_album_with_failed_generation_is_not_retried_per_photo(
    session_factory: async_sessionmaker[AsyncSession], catalog_yaml: Path, telegram: FakeTelegram
) -> None:
    image_gen = FakeImageGenClient(ExternalServiceError("polza", "HTTP 502", 502))
    dp = make_dispatcher(
        session_factory,
        FakeLLMClient(),
        settings=make_settings(catalog_path=catalog_yaml),
        image_gen=image_gen,
        downloader=FakeDownloader({"a": ROOM, "b": ROOM}),
    )
    bot = Bot(token=BOT_TOKEN)

    await dp.feed_update(bot, press_tryon(1))
    await dp.feed_update(bot, photo_update(2, "a", media_group_id="album"))
    await dp.feed_update(bot, photo_update(3, "b", media_group_id="album"))

    assert len(image_gen.calls) == 1
    assert telegram.texts().count(texts.TRYON_FAILED) == 1
    assert await lead_photo_ids(session_factory) == []
