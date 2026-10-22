from aiogram import Router
from aiogram.types import ErrorEvent

from app.bot import texts


async def on_error(event: ErrorEvent) -> bool:
    # The exception is already logged with update context by UpdateLoggingMiddleware.
    message = event.update.message
    if message is not None:
        await message.answer(texts.ERROR_GENERIC)
    return True


def create_router() -> Router:
    router = Router(name="errors")
    router.errors.register(on_error)
    return router
