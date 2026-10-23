import contextlib

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ErrorEvent

from app.bot import texts


async def on_error(event: ErrorEvent) -> bool:
    # The exception is already logged with update context by UpdateLoggingMiddleware.
    message = event.update.message
    if message is not None:
        await message.answer(texts.ERROR_GENERIC)
    callback = event.update.callback_query
    if callback is not None:
        # A query answered before the failure cannot be answered twice: nothing to show then.
        with contextlib.suppress(TelegramAPIError):
            await callback.answer(texts.ERROR_GENERIC, show_alert=True)
    return True


def create_router() -> Router:
    router = Router(name="errors")
    router.errors.register(on_error)
    return router
