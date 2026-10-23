import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageMedia,
    SendMessage,
    SendPhoto,
    TelegramMethod,
)
from aiogram.types import Chat, InlineKeyboardMarkup, InputFile, Message, PhotoSize
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot import texts
from app.bot.keyboards import CATALOG_NOOP, CatalogPage, catalog_keyboard
from app.db.models import CatalogCategory, CatalogItem
from app.db.repositories import CatalogRepository, UnitOfWork
from app.schemas.catalog import CatalogConfig
from app.schemas.qualification import LLMReply
from app.services.catalog import CatalogService, load_catalog
from tests.fakes import (
    BOT_TOKEN,
    FakeCrmClient,
    FakeLLMClient,
    FakeNotifier,
    callback_update,
    image_bytes,
    make_dispatcher,
    make_settings,
    text_update,
)

REPO_CATALOG = Path("assets/catalog/catalog.yaml")
MIN_PER_CATEGORY, MAX_PER_CATEGORY = 5, 8
NOT_MODIFIED = "Bad Request: message is not modified"


def entry(image: str, category: str = "kitchen", title: str = "Кухня") -> dict[str, object]:
    return {"category": category, "title": title, "style": "Модерн", "image": image}


def write_catalog(directory: Path, entries: list[dict[str, object]]) -> Path:
    for item in entries:
        image = directory / str(item["image"])
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(image_bytes())
    path = directory / "catalog.yaml"
    path.write_text(yaml.safe_dump({"items": entries}, allow_unicode=True), encoding="utf-8")
    return path


def catalog_service(session: AsyncSession, images_dir: Path = Path("cat")) -> CatalogService:
    return CatalogService(CatalogRepository(session), UnitOfWork(session), images_dir)


def llm_catalog_json(reply: str, catalog: str | None, ready: bool = False, **slots: object) -> str:
    return json.dumps({"reply": reply, "slots": slots, "ready": ready, "catalog": catalog})


# --- yaml and seed ---


def test_repo_catalog_file_is_valid() -> None:
    catalog = load_catalog(REPO_CATALOG)

    per_category = Counter(item.category for item in catalog.items)
    assert set(per_category) == set(CatalogCategory)
    assert all(MIN_PER_CATEGORY <= n <= MAX_PER_CATEGORY for n in per_category.values())
    assert all(item.source_url for item in catalog.items)


@pytest.mark.parametrize(
    "items",
    [
        [],
        [entry("/etc/passwd.jpg")],
        [entry("../secret.jpg")],
        [entry("kitchen\\a.jpg")],
        [entry("kitchen/a.gif")],
        [entry("kitchen/a.jpg", category="sofa")],
        [entry("kitchen/a.jpg", title="")],
        [entry("kitchen/a.jpg"), entry("kitchen/a.jpg")],
        [{**entry("kitchen/a.jpg"), "price": 1}],
    ],
)
def test_broken_catalog_is_rejected(items: list[dict[str, object]]) -> None:
    with pytest.raises(ValidationError):
        CatalogConfig.model_validate({"items": items})


def test_missing_image_fails_load(tmp_path: Path) -> None:
    path = write_catalog(tmp_path, [entry("kitchen/a.jpg")])
    (tmp_path / "kitchen" / "a.jpg").unlink()

    with pytest.raises(FileNotFoundError):
        load_catalog(path)


async def test_seed_is_idempotent_and_keeps_file_ids(session: AsyncSession) -> None:
    config = CatalogConfig.model_validate(
        {"items": [entry("k/1.jpg"), entry("w/1.jpg", category="wardrobe")]}
    )
    service = catalog_service(session)
    await service.seed(config)
    first = await CatalogRepository(session).list_all()
    await service.remember_file_id(first[0].id, "tg-1")

    await service.seed(config)

    items = await CatalogRepository(session).list_all()
    assert [(i.id, i.image_path) for i in items] == [(1, "k/1.jpg"), (2, "w/1.jpg")]
    assert items[0].tg_file_id == "tg-1"


async def test_seed_updates_changed_and_removes_missing(session: AsyncSession) -> None:
    service = catalog_service(session)
    await service.seed(
        CatalogConfig.model_validate({"items": [entry("k/1.jpg"), entry("k/2.jpg")]})
    )
    await service.remember_file_id(1, "tg-1")

    await service.seed(CatalogConfig.model_validate({"items": [entry("k/1.jpg", title="Новая")]}))

    items = await CatalogRepository(session).list_all()
    assert [(i.image_path, i.title, i.tg_file_id) for i in items] == [("k/1.jpg", "Новая", "tg-1")]


async def test_get_card_wraps_index_and_resolves_photo(session: AsyncSession) -> None:
    service = catalog_service(session, Path("assets/catalog"))
    await service.seed(
        CatalogConfig.model_validate(
            {"items": [entry("k/1.jpg"), entry("k/2.jpg", title="Вторая")]}
        )
    )
    await service.remember_file_id(1, "tg-1")

    first = await service.get_card(CatalogCategory.KITCHEN, 0)
    last = await service.get_card(CatalogCategory.KITCHEN, -1)  # ◀ on the first card

    assert first is not None and last is not None
    assert (first.index, first.total, first.file_id) == (0, 2, "tg-1")
    assert (last.index, last.title, last.file_id) == (1, "Вторая", None)
    assert last.image_path == Path("assets/catalog/k/2.jpg")
    assert await service.get_card(CatalogCategory.KITCHEN, 10**9) is not None
    assert await service.get_card(CatalogCategory.INTERIOR, 0) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("kitchen", CatalogCategory.KITCHEN),
        (" Walk_In ", CatalogCategory.WALK_IN),
        ("sofa", None),
        ("", None),
        (None, None),
        (5, None),
    ],
)
def test_llm_catalog_field_is_lenient(raw: object, expected: CatalogCategory | None) -> None:
    reply = LLMReply.model_validate({"reply": "Ок", "catalog": raw})

    assert reply.catalog is expected


# --- keyboard ---


def _callbacks(markup: InlineKeyboardMarkup) -> list[list[str | None]]:
    return [[button.callback_data for button in row] for row in markup.inline_keyboard]


def test_keyboard_pages_wrap_and_marks_current_category() -> None:
    rows = _callbacks(catalog_keyboard(CatalogCategory.KITCHEN, 0, 3, item_id=7))

    assert rows[0] == ["cat:kitchen:-1", CATALOG_NOOP, "cat:kitchen:1"]
    assert rows[1] == ["try:7"]
    assert rows[2:] == [[CATALOG_NOOP, "cat:wardrobe:0"], ["cat:walk_in:0", "cat:interior:0"]]


def test_keyboard_without_paging_for_single_card() -> None:
    rows = _callbacks(catalog_keyboard(CatalogCategory.INTERIOR, 0, 1, item_id=7))

    assert all("cat:interior" not in str(row) for row in rows)
    assert len(rows) == 3  # try-on and the category switcher


# --- handlers through the dispatcher ---


class FakeTelegram:
    """Bot API stand-in: photo sends/edits return a Message with a fresh file_id."""

    def __init__(self) -> None:
        self.calls: list[TelegramMethod[Any]] = []
        self.rejected_file_ids: set[str] = set()
        self.not_modified = False
        self.fail_uploads = False
        self._uploads = 0

    async def handle(self, method: TelegramMethod[Any]) -> Any:
        self.calls.append(method)
        if isinstance(method, SendPhoto):
            return self._photo_result(method, method.photo)
        if isinstance(method, EditMessageMedia):
            return self._photo_result(method, method.media.media)
        return True

    def _photo_result(self, method: TelegramMethod[Any], photo: str | InputFile) -> Message:
        if isinstance(photo, InputFile):
            if self.fail_uploads:
                raise TelegramBadRequest(method, "Bad Request: wrong file")
            self._uploads += 1
            file_id = f"tg-{self._uploads}"
        else:
            if self.not_modified:
                raise TelegramBadRequest(method, NOT_MODIFIED)
            if photo in self.rejected_file_ids:
                raise TelegramBadRequest(method, "Bad Request: wrong file identifier")
            file_id = photo
        return Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=500, type="private"),
            photo=[PhotoSize(file_id=file_id, file_unique_id=file_id, width=1280, height=850)],
        )

    def of(self, kind: type[TelegramMethod[Any]]) -> list[Any]:
        return [m for m in self.calls if isinstance(m, kind)]


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> FakeTelegram:
    fake = FakeTelegram()

    async def fake_call(self: Bot, method: TelegramMethod[Any], request_timeout: int | None = None):
        return await fake.handle(method)

    monkeypatch.setattr(Bot, "__call__", fake_call)
    return fake


@pytest.fixture
async def seeded(tmp_path: Path, session_factory: async_sessionmaker[AsyncSession]) -> Path:
    """Two kitchens and one wardrobe on disk and in the DB; returns catalog.yaml."""
    path = write_catalog(
        tmp_path,
        [
            entry("kitchen/1.jpg", title="Белая <кухня>"),
            entry("kitchen/2.jpg", title="Чёрная кухня"),
            entry("wardrobe/1.jpg", category="wardrobe", title="Шкаф"),
        ],
    )
    async with session_factory() as session:
        await catalog_service(session, tmp_path).seed(load_catalog(path))
    return path


async def cached_file_ids(session_factory: async_sessionmaker[AsyncSession]) -> list[str | None]:
    async with session_factory() as session:
        return list((await session.scalars(select(CatalogItem.tg_file_id))).all())


async def test_dialog_turn_shows_card_and_caches_file_id(
    session_factory: async_sessionmaker[AsyncSession], seeded: Path, telegram: FakeTelegram
) -> None:
    llm = FakeLLMClient(
        llm_catalog_json("Показываю примеры кухонь 👇", "kitchen"),
        llm_catalog_json("Вот они ещё раз", "kitchen"),
    )
    dp = make_dispatcher(session_factory, llm, settings=make_settings(catalog_path=seeded))
    bot = Bot(token=BOT_TOKEN)

    await dp.feed_update(bot, text_update(1, "Покажите кухни"))
    await dp.feed_update(bot, text_update(2, "Ещё раз"))

    replies = [m.text for m in telegram.of(SendMessage)]
    first, second = telegram.of(SendPhoto)
    assert replies == ["Показываю примеры кухонь 👇", "Вот они ещё раз"]
    assert isinstance(first.photo, InputFile)  # uploaded from disk once...
    assert second.photo == "tg-1"  # ...then sent by the cached file_id
    assert first.caption == "<b>Белая &lt;кухня&gt;</b>\n🎨 Модерн"
    assert first.reply_markup.inline_keyboard[0][1].text == "1 / 2"
    assert await cached_file_ids(session_factory) == ["tg-1", None, None]
    # Past answers are replayed in the full reply shape, catalog included.
    assert json.loads(llm.calls[1][-2].content)["catalog"] is None


async def test_page_callback_edits_card_in_place(
    session_factory: async_sessionmaker[AsyncSession], seeded: Path, telegram: FakeTelegram
) -> None:
    dp = make_dispatcher(
        session_factory, FakeLLMClient(), settings=make_settings(catalog_path=seeded)
    )
    data = CatalogPage(category=CatalogCategory.KITCHEN, index=1).pack()

    await dp.feed_update(Bot(token=BOT_TOKEN), callback_update(1, data))

    (edit,) = telegram.of(EditMessageMedia)
    assert edit.media.caption == "<b>Чёрная кухня</b>\n🎨 Модерн"
    assert edit.reply_markup.inline_keyboard[0][1].text == "2 / 2"
    assert len(telegram.of(AnswerCallbackQuery)) == 1
    assert telegram.of(SendPhoto) == []
    assert await cached_file_ids(session_factory) == [None, "tg-1", None]


async def test_rejected_file_id_is_reuploaded(
    session_factory: async_sessionmaker[AsyncSession], seeded: Path, telegram: FakeTelegram
) -> None:
    async with session_factory() as session:
        await catalog_service(session).remember_file_id(3, "stale")
    telegram.rejected_file_ids.add("stale")
    dp = make_dispatcher(
        session_factory, FakeLLMClient(), settings=make_settings(catalog_path=seeded)
    )
    data = CatalogPage(category=CatalogCategory.WARDROBE, index=0).pack()

    await dp.feed_update(Bot(token=BOT_TOKEN), callback_update(1, data))

    stale, fresh = telegram.of(EditMessageMedia)
    assert (stale.media.media, isinstance(fresh.media.media, InputFile)) == ("stale", True)
    assert await cached_file_ids(session_factory) == [None, None, "tg-1"]


async def test_double_tap_not_modified_is_ignored(
    session_factory: async_sessionmaker[AsyncSession], seeded: Path, telegram: FakeTelegram
) -> None:
    async with session_factory() as session:
        await catalog_service(session).remember_file_id(1, "tg-1")
    telegram.not_modified = True
    dp = make_dispatcher(
        session_factory, FakeLLMClient(), settings=make_settings(catalog_path=seeded)
    )
    data = CatalogPage(category=CatalogCategory.KITCHEN, index=0).pack()

    await dp.feed_update(Bot(token=BOT_TOKEN), callback_update(1, data))

    assert len(telegram.of(EditMessageMedia)) == 1  # no re-upload
    (answer,) = telegram.of(AnswerCallbackQuery)
    assert answer.text is None


async def test_upload_failure_answers_alert(
    session_factory: async_sessionmaker[AsyncSession], seeded: Path, telegram: FakeTelegram
) -> None:
    telegram.fail_uploads = True
    dp = make_dispatcher(
        session_factory, FakeLLMClient(), settings=make_settings(catalog_path=seeded)
    )
    data = CatalogPage(category=CatalogCategory.KITCHEN, index=0).pack()

    await dp.feed_update(Bot(token=BOT_TOKEN), callback_update(1, data))

    (answer,) = telegram.of(AnswerCallbackQuery)
    assert (answer.text, answer.show_alert) == (texts.ERROR_GENERIC, True)


async def test_empty_category_answers_text(
    session_factory: async_sessionmaker[AsyncSession], seeded: Path, telegram: FakeTelegram
) -> None:
    llm = FakeLLMClient(llm_catalog_json("Вот интерьеры", "interior"))
    dp = make_dispatcher(session_factory, llm, settings=make_settings(catalog_path=seeded))

    await dp.feed_update(Bot(token=BOT_TOKEN), text_update(1, "Покажите интерьеры"))

    assert [m.text for m in telegram.of(SendMessage)] == ["Вот интерьеры", texts.CATALOG_EMPTY]
    assert telegram.of(SendPhoto) == []


async def test_noop_and_forged_callbacks(
    session_factory: async_sessionmaker[AsyncSession], seeded: Path, telegram: FakeTelegram
) -> None:
    dp = make_dispatcher(
        session_factory, FakeLLMClient(), settings=make_settings(catalog_path=seeded)
    )
    bot = Bot(token=BOT_TOKEN)

    await dp.feed_update(bot, callback_update(1, CATALOG_NOOP))
    await dp.feed_update(bot, callback_update(2, "cat:sofa:0"))  # not a category: unhandled

    assert len(telegram.of(AnswerCallbackQuery)) == 1
    assert telegram.of(EditMessageMedia) == []


async def test_failed_card_does_not_block_lead_delivery(
    session_factory: async_sessionmaker[AsyncSession], seeded: Path, telegram: FakeTelegram
) -> None:
    telegram.fail_uploads = True
    llm = FakeLLMClient(
        llm_catalog_json(
            "Спасибо! Вот примеры",
            "kitchen",
            ready=True,
            furniture_type="kitchen",
            dimensions="3 м",
            length_m=3,
            phone="89123451234",
        )
    )
    crm, notifier = FakeCrmClient(), FakeNotifier()
    dp = make_dispatcher(
        session_factory,
        llm,
        settings=make_settings(catalog_path=seeded),
        crm=crm,
        notifier=notifier,
    )

    await dp.feed_update(Bot(token=BOT_TOKEN), text_update(1, "Кухня 3 м, 89123451234"))

    replies = [m.text for m in telegram.of(SendMessage)]
    estimate = texts.ESTIMATE_RANGE.format(
        min_price=texts.format_price(115000, "₽"), max_price=texts.format_price(155000, "₽")
    )
    # Thank-you, then the estimate, then the card (failed here); never the generic error.
    assert replies == ["Спасибо! Вот примеры", estimate, texts.CATALOG_FAILED]
    assert len(crm.sent) == 1
    assert len(notifier.cards) == 1
