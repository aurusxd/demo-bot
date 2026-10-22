from aiogram import F, Router
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from app.bot import texts
from app.core.errors import InvalidInputError
from app.db.models import MessageKind, User
from app.services.qualification import QualificationService


async def run_dialog_turn(
    message: Message,
    user: User,
    qualification: QualificationService,
    text: str,
    kind: MessageKind = MessageKind.TEXT,
    too_long_text: str = texts.TEXT_TOO_LONG,
) -> None:
    """One LLM dialog turn for any text source (typed text, voice transcript, photo caption)."""
    bot = message.bot
    assert bot is not None  # always bound for incoming updates
    try:
        async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
            result = await qualification.handle_message(user, text, kind)
    except InvalidInputError:
        await message.answer(too_long_text)
        return

    # LLM output is plain text: never parse it as HTML. On qualification the LLM itself
    # thanks the client and recaps the order, so no robotic summary is sent here.
    await message.answer(result.reply or texts.QUALIFY_FALLBACK, parse_mode=None)


async def dialog_message(message: Message, user: User, qualification: QualificationService) -> None:
    await run_dialog_turn(message, user, qualification, message.text or "")


def create_router() -> Router:
    router = Router(name="qualify")
    # Any free text is a dialog turn (commands are matched by earlier routers).
    router.message.register(dialog_message, F.text, ~F.text.startswith("/"))
    return router
