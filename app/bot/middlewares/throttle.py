import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update, User
from loguru import logger

from app.bot import texts

# Any: aiogram middleware contract (handler data dict and result are untyped by design).
Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class ThrottlingMiddleware(BaseMiddleware):
    """Sliding-window limit of updates per user: protects the LLM budget and the DB.

    Over the limit the update is dropped before any DB/LLM work; the user is warned once
    per burst, further updates in the same burst are dropped silently.
    """

    def __init__(
        self,
        max_updates: int,
        window_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_updates = max_updates
        self._window_s = window_s
        self._clock = clock
        self._hits: dict[int, deque[float]] = {}
        self._warned: set[int] = set()

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if not isinstance(user, User):
            return await handler(event, data)

        now = self._clock()
        hits = self._hits.setdefault(user.id, deque())
        while hits and now - hits[0] >= self._window_s:
            hits.popleft()

        if len(hits) >= self._max_updates:
            logger.warning("throttled hits={} window_s={}", len(hits), self._window_s)
            if user.id not in self._warned:
                self._warned.add(user.id)
                await _notify(event)
            return None

        hits.append(now)
        self._warned.discard(user.id)
        return await handler(event, data)


async def _notify(event: TelegramObject) -> None:
    if isinstance(event, Update) and event.message is not None:
        await event.message.answer(texts.THROTTLED)
