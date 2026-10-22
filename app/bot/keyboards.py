from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from app.bot import texts


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=texts.BTN_LEAD)],
            [KeyboardButton(text=texts.BTN_CATALOG), KeyboardButton(text=texts.BTN_TRYON)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder=texts.MENU_PLACEHOLDER,
    )
