from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from app.core.locks import UserLocks

# Any: aiogram middleware contract (handler data dict and result are untyped by design).
Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class UserLockMiddleware(BaseMiddleware):
    """Processes one user's updates strictly one by one (different users stay parallel).

    aiogram handles updates concurrently, so two quick messages of one user would race on
    the same lead: duplicated drafts, mixed history, replies out of order. The same locks
    guard background jobs that touch the user's leads (lead delivery retry).
    """

    def __init__(self, locks: UserLocks) -> None:
        self._locks = locks

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if not isinstance(user, User):
            return await handler(event, data)
        async with self._locks.get(user.id):
            return await handler(event, data)
