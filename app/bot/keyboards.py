from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot import texts
from app.db.models import CatalogCategory

# Buttons that only label something (counter, current category): answered, nothing changes.
CATALOG_NOOP = "cat_noop"
CATEGORIES_PER_ROW = 2


class CatalogPage(CallbackData, prefix="cat"):
    """Show the `index`-th card of a category. Client-controlled: the service wraps index."""

    category: CatalogCategory
    index: int


def catalog_keyboard(category: CatalogCategory, index: int, total: int) -> InlineKeyboardMarkup:
    """◀ n / total ▶ (only if there is more than one card) and the category switcher."""
    rows: list[list[InlineKeyboardButton]] = []
    if total > 1:
        rows.append(
            [
                _page_button(texts.CATALOG_PREV, category, index - 1),
                _noop_button(texts.CATALOG_COUNTER.format(index=index + 1, total=total)),
                _page_button(texts.CATALOG_NEXT, category, index + 1),
            ]
        )
    categories = [_category_button(c, current=c is category) for c in CatalogCategory]
    rows.extend(
        categories[i : i + CATEGORIES_PER_ROW]
        for i in range(0, len(categories), CATEGORIES_PER_ROW)
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _category_button(category: CatalogCategory, current: bool) -> InlineKeyboardButton:
    label = texts.CATALOG_CATEGORY_LABELS[category]
    if current:
        return _noop_button(texts.CATALOG_CURRENT_CATEGORY.format(label=label))
    return _page_button(label, category, 0)


def _page_button(text: str, category: CatalogCategory, index: int) -> InlineKeyboardButton:
    data = CatalogPage(category=category, index=index).pack()
    return InlineKeyboardButton(text=text, callback_data=data)


def _noop_button(text: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=CATALOG_NOOP)
