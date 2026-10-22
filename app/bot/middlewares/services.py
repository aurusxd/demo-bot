from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.base import CrmClient, LeadNotifier, LLMClient
from app.db.repositories import (
    LeadMediaRepository,
    LeadRepository,
    MessageRepository,
    UnitOfWork,
)
from app.services.estimate import EstimateService
from app.services.leads import LeadService
from app.services.media import MediaService
from app.services.qualification import QualificationService

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
        estimator: EstimateService,
        crm: CrmClient,
        notifier: LeadNotifier,
    ) -> None:
        self._llm = llm
        self._media = media  # stateless (clients only), shared by all updates
        self._estimator = estimator
        self._crm = crm
        self._notifier = notifier

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
            data["leads"] = LeadService(
                self._estimator,
                self._crm,
                self._notifier,
                LeadRepository(session),
                LeadMediaRepository(session),
                UnitOfWork(session),
            )
        data["media"] = self._media
        return await handler(event, data)
