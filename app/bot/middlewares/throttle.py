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

    Button presses (callback queries) never reach the LLM, so they have their own, higher
    limit and do not eat the dialog's budget: paging the catalog must not block a message.

    Over the limit the update is dropped before any DB/LLM work. A message warns once per
    burst, further ones are dropped silently; a dropped button press is always answered,
    otherwise its spinner hangs.
    """

    def __init__(
        self,
        max_updates: int,
        window_s: float,
        max_callbacks: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limits = {False: max_updates, True: max_callbacks or max_updates}
        self._window_s = window_s
        self._clock = clock
        self._hits: dict[tuple[int, bool], deque[float]] = {}
        self._warned: set[int] = set()

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if not isinstance(user, User):
            return await handler(event, data)

        callback = event.callback_query if isinstance(event, Update) else None
        key = (user.id, callback is not None)
        now = self._clock()
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self._window_s:
            hits.popleft()

        if len(hits) >= self._limits[key[1]]:
            logger.warning(
                "throttled hits={} window_s={} callback={}",
                len(hits),
                self._window_s,
                callback is not None,
            )
            if callback is not None:
                await callback.answer(texts.THROTTLED)
            elif user.id not in self._warned:
                self._warned.add(user.id)
                await _notify(event)
            return None

        hits.append(now)
        if callback is None:
            self._warned.discard(user.id)
        return await handler(event, data)


async def _notify(event: TelegramObject) -> None:
    if isinstance(event, Update) and event.message is not None:
        await event.message.answer(texts.THROTTLED)
