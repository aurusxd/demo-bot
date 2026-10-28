import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.base import ImageGenClient
from app.core.errors import (
    CatalogItemNotFoundError,
    ExternalServiceError,
    TryOnFailedError,
    TryOnLimitError,
)
from app.db.models import CatalogCategory, CatalogItem, TryOnRequest, TryOnStatus, User
from app.db.repositories import (
    CatalogRepository,
    TryOnRequestRepository,
    UnitOfWork,
    UserRepository,
)
from app.prompts.tryon import tryon_prompt
from app.services.media import fit_jpeg

# The whole generation (reference read + image API with its retry/polling + download).
TRYON_BUDGET_S = 120.0
MAX_ERROR_LEN = 255


def utc_today() -> date:
    return datetime.now(UTC).date()


@dataclass(frozen=True, slots=True)
class TryOnItem:
    item_id: int
    category: CatalogCategory
    title: str


@dataclass(frozen=True, slots=True)
class TryOnResult:
    item: TryOnItem
    image: bytes  # JPEG from the image API; sent to the user and dropped, never stored


class TryOnService:
    """Puts a catalog item into the client's room photo via the image API.

    Daily limit per user (users.tryon_used_today); a failed generation is not counted.
    Every attempt is recorded in tryon_requests with its status and latency.
    """

    def __init__(
        self,
        image_gen: ImageGenClient,
        users: UserRepository,
        items: CatalogRepository,
        requests: TryOnRequestRepository,
        uow: UnitOfWork,
        images_dir: Path,
        daily_limit: int,
        unlimited_tg_ids: frozenset[int] = frozenset(),
        today: Callable[[], date] = utc_today,
        budget_s: float = TRYON_BUDGET_S,
    ) -> None:
        self._image_gen = image_gen
        self._users = users
        self._items = items
        self._requests = requests
        self._uow = uow
        self._images_dir = images_dir
        self._daily_limit = daily_limit
        self._unlimited_tg_ids = unlimited_tg_ids
        self._today = today
        self._budget_s = budget_s

    async def get_item(self, item_id: int) -> TryOnItem:
        """Raises CatalogItemNotFoundError: callback data is client-controlled."""
        item = await self._items.get(item_id)
        if item is None:
            logger.info("tryon item not found item_id={}", item_id)
            raise CatalogItemNotFoundError(f"catalog item {item_id}")
        return TryOnItem(item.id, item.category, item.title)

    def ensure_quota(self, user: User) -> None:
        """Raises TryOnLimitError if today's generations are used up (nothing is spent)."""
        if self._is_unlimited(user):
            return
        used = self._used_today(user)
        if used >= self._daily_limit:
            logger.info("tryon limit reached used={} limit={}", used, self._daily_limit)
            raise TryOnLimitError(f"{used}/{self._daily_limit}")

    async def generate(self, user: User, item_id: int, room_photo: bytes) -> TryOnResult:
        """Room photo must be a prepared JPEG (MediaService.prepare_tryon_photo)."""
        catalog_item = await self._items.get(item_id)
        if catalog_item is None:
            raise CatalogItemNotFoundError(f"catalog item {item_id}")
        self.ensure_quota(user)
        item = TryOnItem(catalog_item.id, catalog_item.category, catalog_item.title)
        request = await self._start(user, catalog_item)
        # Committed before the slow API call: SQLite's write lock is not held while waiting.
        await self._uow.commit()

        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._budget_s):
                image = await self._render(catalog_item, room_photo)
        except (ExternalServiceError, TimeoutError, OSError) as exc:
            await self._fail(user, request, _elapsed_ms(started), exc)
            raise TryOnFailedError(str(request.id)) from exc
        await self._finish(request, TryOnStatus.DONE, _elapsed_ms(started))
        return TryOnResult(item, image)

    async def _render(self, item: CatalogItem, room_photo: bytes) -> bytes:
        reference = await asyncio.to_thread(_read_reference, self._images_dir / item.image_path)
        return await self._image_gen.edit(
            room_photo, reference, tryon_prompt(item.category, item.title, item.style)
        )

    async def _start(self, user: User, item: CatalogItem) -> TryOnRequest:
        if not self._is_unlimited(user):
            user.tryon_used_today = self._used_today(user) + 1
            user.tryon_reset_date = self._today()
            await self._users.save(user)
        request = await self._requests.add(
            TryOnRequest(user_id=user.id, catalog_item_id=item.id, status=TryOnStatus.PENDING)
        )
        logger.info(
            "tryon started request_id={} item_id={} used_today={}",
            request.id,
            item.id,
            user.tryon_used_today,
        )
        return request

    async def _fail(
        self, user: User, request: TryOnRequest, latency_ms: int, exc: BaseException
    ) -> None:
        # The user got no picture: give the attempt back.
        if not self._is_unlimited(user) and user.tryon_used_today > 0:
            user.tryon_used_today -= 1
            await self._users.save(user)
        error = str(exc) or type(exc).__name__
        await self._finish(request, TryOnStatus.FAILED, latency_ms, error[:MAX_ERROR_LEN])

    async def _finish(
        self, request: TryOnRequest, status: TryOnStatus, latency_ms: int, error: str | None = None
    ) -> None:
        request.status = status
        request.latency_ms = latency_ms
        request.error = error
        await self._requests.save(request)
        await self._uow.commit()
        log = logger.warning if status is TryOnStatus.FAILED else logger.info
        log(
            "tryon {} request_id={} latency_ms={} error={}",
            status.value,
            request.id,
            latency_ms,
            error,
        )

    def _used_today(self, user: User) -> int:
        return user.tryon_used_today if user.tryon_reset_date == self._today() else 0

    def _is_unlimited(self, user: User) -> bool:
        return user.tg_id in self._unlimited_tg_ids


@dataclass(frozen=True, slots=True)
class TryOnServiceFactory:
    """Shared image client and settings; builds a TryOnService per update's DB session."""

    image_gen: ImageGenClient
    images_dir: Path
    daily_limit: int
    unlimited_tg_ids: frozenset[int] = frozenset()

    def build(self, session: AsyncSession) -> TryOnService:
        return TryOnService(
            self.image_gen,
            UserRepository(session),
            CatalogRepository(session),
            TryOnRequestRepository(session),
            UnitOfWork(session),
            self.images_dir,
            self.daily_limit,
            self.unlimited_tg_ids,
        )


def _read_reference(path: Path) -> bytes:
    """Blocking (file + Pillow): run in a worker thread. The catalog photo as a JPEG."""
    return fit_jpeg(path.read_bytes())


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
