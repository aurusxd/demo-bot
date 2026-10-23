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


class TryOnStart(CallbackData, prefix="try"):
    """«Попробовать у себя» under a catalog card. Client-controlled: the item may be gone."""

    item_id: int


class TryOnMore(CallbackData, prefix="try_more"):
    """«Другая мебель» under a try-on result: the catalog as a new card, the result stays."""

    category: CatalogCategory


class TryOnLead(CallbackData, prefix="try_lead"):
    """«Оставить заявку» under a try-on result: continues the dialog about this item."""

    item_id: int


def catalog_keyboard(
    category: CatalogCategory, index: int, total: int, item_id: int
) -> InlineKeyboardMarkup:
    """◀ n / total ▶ (only if there is more than one card), try-on, the category switcher."""
    rows: list[list[InlineKeyboardButton]] = []
    if total > 1:
        rows.append(
            [
                _page_button(texts.CATALOG_PREV, category, index - 1),
                _noop_button(texts.CATALOG_COUNTER.format(index=index + 1, total=total)),
                _page_button(texts.CATALOG_NEXT, category, index + 1),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text=texts.TRYON_BUTTON, callback_data=TryOnStart(item_id=item_id).pack()
            )
        ]
    )
    categories = [_category_button(c, current=c is category) for c in CatalogCategory]
    rows.extend(
        categories[i : i + CATEGORIES_PER_ROW]
        for i in range(0, len(categories), CATEGORIES_PER_ROW)
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tryon_result_keyboard(category: CatalogCategory, item_id: int) -> InlineKeyboardMarkup:
    more = TryOnMore(category=category).pack()
    lead = TryOnLead(item_id=item_id).pack()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=texts.TRYON_MORE_BUTTON, callback_data=more),
                InlineKeyboardButton(text=texts.TRYON_LEAD_BUTTON, callback_data=lead),
            ]
        ]
    )


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
