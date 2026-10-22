from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.base import LLMClient
from app.db.repositories import LeadRepository, MessageRepository
from app.services.qualification import QualificationService

# Any: aiogram middleware contract (handler data dict and result are untyped by design).
Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class ServicesMiddleware(BaseMiddleware):
    """Per-update composition root: builds services over the update's DB session.

    Must run after DbSessionMiddleware (needs data["session"]).
    """

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        session = data.get("session")
        if isinstance(session, AsyncSession):
            data["qualification"] = QualificationService(
                self._llm, LeadRepository(session), MessageRepository(session)
            )
        return await handler(event, data)
