from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove
from loguru import logger

from app.bot import texts
from app.db.models import User
from app.services.qualification import QualificationService
from app.services.users import UserService

DELETE_ME_COMMAND = "delete_me"


async def cmd_start(message: Message, user: User, qualification: QualificationService) -> None:
    """Starts a fresh demo dialog. No menu: the bot talks like a live manager."""
    await qualification.start(user, texts.GREETING)
    # ReplyKeyboardRemove also hides the old menu keyboard for users who still have it.
    await message.answer(texts.GREETING, reply_markup=ReplyKeyboardRemove())


async def cmd_delete_me(
    message: Message, user: User, users: UserService, state: FSMContext
) -> None:
    """Erases the user with all their leads; a later message starts from scratch."""
    await users.delete(user)
    await state.clear()  # e.g. a pending try-on must not outlive the user's data
    logger.info("fsm state cleared (delete_me)")
    await message.answer(texts.DELETE_ME_DONE)


def create_router() -> Router:
    router = Router(name="start")
    router.message.register(cmd_start, CommandStart())
    router.message.register(cmd_delete_me, Command(DELETE_ME_COMMAND))
    return router
