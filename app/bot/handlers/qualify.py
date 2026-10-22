from aiogram import F, Router
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from app.bot import texts
from app.core.errors import InvalidInputError
from app.db.models import User
from app.services.qualification import QualificationService


async def dialog_message(message: Message, user: User, qualification: QualificationService) -> None:
    text = message.text or ""
    bot = message.bot
    assert bot is not None  # always bound for incoming updates
    try:
        async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
            result = await qualification.handle_message(user, text)
    except InvalidInputError:
        await message.answer(texts.TEXT_TOO_LONG)
        return

    # LLM output is plain text: never parse it as HTML. On qualification the LLM itself
    # thanks the client and recaps the order, so no robotic summary is sent here.
    await message.answer(result.reply or texts.QUALIFY_FALLBACK, parse_mode=None)


def create_router() -> Router:
    router = Router(name="qualify")
    # Any free text is a dialog turn (commands are matched by earlier routers).
    router.message.register(dialog_message, F.text, ~F.text.startswith("/"))
    return router
