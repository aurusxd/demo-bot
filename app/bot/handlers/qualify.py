from aiogram import F, Router
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender

from app.bot import keyboards, texts
from app.core.errors import InvalidInputError
from app.db.models import User
from app.services.qualification import QualificationService


async def start_lead(message: Message, user: User, qualification: QualificationService) -> None:
    await qualification.start(user, texts.QUALIFY_START)
    await message.answer(texts.QUALIFY_START, reply_markup=keyboards.main_menu())


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

    # LLM output and user data are plain text: never parse them as HTML.
    await message.answer(result.reply or texts.QUALIFY_FALLBACK, parse_mode=None)
    if result.qualified:
        summary = texts.LEAD_QUALIFIED.format(summary=texts.lead_summary(result.slots))
        await message.answer(summary, parse_mode=None, reply_markup=keyboards.main_menu())


def create_router() -> Router:
    router = Router(name="qualify")
    router.message.register(start_lead, F.text == texts.BTN_LEAD)
    # Any other free text is a qualification turn (commands and menu buttons are matched earlier).
    router.message.register(dialog_message, F.text, ~F.text.startswith("/"))
    return router
