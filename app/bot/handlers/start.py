from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message, ReplyKeyboardRemove

from app.bot import texts
from app.db.models import User
from app.services.qualification import QualificationService


async def cmd_start(message: Message, user: User, qualification: QualificationService) -> None:
    """Starts a fresh demo dialog. No menu: the bot talks like a live manager."""
    await qualification.start(user, texts.GREETING)
    # ReplyKeyboardRemove also hides the old menu keyboard for users who still have it.
    await message.answer(texts.GREETING, reply_markup=ReplyKeyboardRemove())


def create_router() -> Router:
    router = Router(name="start")
    router.message.register(cmd_start, CommandStart())
    return router
