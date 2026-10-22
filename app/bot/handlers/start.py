from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.bot import keyboards, texts

MENU_BUTTONS = {texts.BTN_LEAD, texts.BTN_CATALOG, texts.BTN_TRYON}


async def cmd_start(message: Message) -> None:
    await message.answer(texts.START, reply_markup=keyboards.main_menu())


# Placeholder until the lead (slice 2), catalog (5) and try-on (6) slices land.
async def menu_coming_soon(message: Message) -> None:
    await message.answer(texts.COMING_SOON, reply_markup=keyboards.main_menu())


def create_router() -> Router:
    router = Router(name="start")
    router.message.register(cmd_start, CommandStart())
    router.message.register(menu_coming_soon, F.text.in_(MENU_BUTTONS))
    return router
