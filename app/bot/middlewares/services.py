from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.base import LLMClient
from app.db.repositories import (
    CatalogRepository,
    LeadMediaRepository,
    LeadRepository,
    MessageRepository,
    UnitOfWork,
)
from app.services.catalog import CatalogService
from app.services.leads import LeadServiceFactory
from app.services.media import MediaService
from app.services.qualification import QualificationService
from app.services.tryon import TryOnServiceFactory

# Any: aiogram middleware contract (handler data dict and result are untyped by design).
Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class ServicesMiddleware(BaseMiddleware):
    """Per-update composition root: builds services over the update's DB session.

    Must run after DbSessionMiddleware (needs data["session"]).
    """

    def __init__(
        self,
        llm: LLMClient,
        media: MediaService,
        lead_services: LeadServiceFactory,
        tryon_services: TryOnServiceFactory,
        catalog_dir: Path,
    ) -> None:
        self._llm = llm
        self._media = media  # stateless (clients only), shared by all updates
        self._lead_services = lead_services
        self._tryon_services = tryon_services
        self._catalog_dir = catalog_dir

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        session = data.get("session")
        if isinstance(session, AsyncSession):
            data["qualification"] = QualificationService(
                self._llm,
                LeadRepository(session),
                MessageRepository(session),
                LeadMediaRepository(session),
                UnitOfWork(session),
            )
            data["leads"] = self._lead_services.build(session)
            data["tryon"] = self._tryon_services.build(session)
            data["catalog"] = CatalogService(
                CatalogRepository(session), UnitOfWork(session), self._catalog_dir
            )
        data["media"] = self._media
        return await handler(event, data)
