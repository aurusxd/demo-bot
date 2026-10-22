import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.dispatcher.event.handler import HandlerObject
from aiogram.enums import ContentType
from aiogram.types import TelegramObject, Update, User
from loguru import logger

# Any: aiogram middleware contract (handler data dict and result are untyped by design).
Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class UpdateLoggingMiddleware(BaseMiddleware):
    """Outer update middleware: binds log context and logs each update with its duration."""

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)

        user = data.get("event_from_user")
        tg_id = user.id if isinstance(user, User) else None
        with logger.contextualize(update_id=event.update_id, tg_id=tg_id):
            logger.info("update received type={} {}", event.event_type, _describe(event))
            started = time.perf_counter()
            try:
                result = await handler(event, data)
            except Exception:
                logger.exception("update failed duration_ms={}", _elapsed_ms(started))
                raise
            logger.info(
                "update done handled={} duration_ms={}",
                result is not UNHANDLED,
                _elapsed_ms(started),
            )
            return result


class HandlerLoggingMiddleware(BaseMiddleware):
    """Inner middleware: logs which handler processed the event and how long it took."""

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        handler_obj = data.get("handler")
        name = handler_obj.callback.__name__ if isinstance(handler_obj, HandlerObject) else "?"
        started = time.perf_counter()
        try:
            return await handler(event, data)
        finally:
            logger.info("handler={} duration_ms={}", name, _elapsed_ms(started))


def _describe(update: Update) -> str:
    # Never log message text itself: only its kind and length.
    message = update.message
    if message is None:
        return ""
    if message.text is not None:
        return f"content=text text_len={len(message.text)}"
    if message.voice is not None:
        return f"content=voice duration_s={message.voice.duration}"
    # aiogram returns a ContentType enum typed as str: log "photo", not "ContentType.PHOTO".
    content_type = message.content_type
    if isinstance(content_type, ContentType):
        content_type = content_type.value
    return f"content={content_type}"


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
