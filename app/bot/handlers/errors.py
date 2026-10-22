from aiogram import Router
from aiogram.types import ErrorEvent

from app.bot import texts

router = Router(name="errors")


@router.errors()
async def on_error(event: ErrorEvent) -> bool:
    # The exception is already logged with update context by UpdateLoggingMiddleware.
    message = event.update.message
    if message is not None:
        await message.answer(texts.ERROR_GENERIC)
    return True
