from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories import UnitOfWork, UserRepository
from app.services.users import UserService

# Any: aiogram middleware contract (handler data dict and result are untyped by design).
Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class UserMiddleware(BaseMiddleware):
    """Registers the sender in DB and exposes it to handlers as data["user"].

    Must run after DbSessionMiddleware (needs data["session"]).
    """

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        tg_user = data.get("event_from_user")
        session = data.get("session")
        if isinstance(tg_user, TgUser) and isinstance(session, AsyncSession):
            service = UserService(UserRepository(session), UnitOfWork(session))
            data["user"] = await service.register(tg_user.id, tg_user.username)
        return await handler(event, data)
