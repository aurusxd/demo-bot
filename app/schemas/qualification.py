import re
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app.db.models import CatalogCategory, FurnitureType, PriceClass

SHORT_SLOT_MAX_LEN = 128
LONG_SLOT_MAX_LEN = 1000
REPLY_MAX_LEN = 2000
PHONE_MIN_DIGITS = 10
PHONE_MAX_DIGITS = 15
MIN_LENGTH_M = 0.5
MAX_LENGTH_M = 20.0


def _clean_text(max_len: int) -> BeforeValidator:
    """LLM output is untrusted: strip, drop empties, truncate instead of failing."""

    def clean(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text[:max_len] or None

    return BeforeValidator(clean)


def _lenient_enum[E: StrEnum](enum_cls: type[E]) -> BeforeValidator:
    """Unknown or empty value -> None: a model typo never breaks the whole reply."""

    def parse(value: object) -> E | None:
        try:
            return enum_cls(str(value).strip().lower()) if value else None
        except ValueError:
            return None

    return BeforeValidator(parse)


def _length_m(value: object) -> float | None:
    """Total linear meters; out-of-range or non-numeric -> None (no estimate from junk)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        length = float(str(value).replace(",", ".").strip())
    except ValueError:
        return None
    return round(length, 2) if MIN_LENGTH_M <= length <= MAX_LENGTH_M else None


def normalize_phone(value: object) -> str | None:
    """'8 (912) 345-12-34' -> '+79123451234'. Anything that is not a phone -> None."""
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 10:
        digits = "7" + digits
    elif len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    if not PHONE_MIN_DIGITS <= len(digits) <= PHONE_MAX_DIGITS:
        return None
    return "+" + digits


ShortSlot = Annotated[str | None, _clean_text(SHORT_SLOT_MAX_LEN)]
LongSlot = Annotated[str | None, _clean_text(LONG_SLOT_MAX_LEN)]


class LeadSlotsPatch(BaseModel):
    """Slots extracted by the LLM on one turn. None means "unchanged"."""

    model_config = ConfigDict(extra="ignore")

    name: ShortSlot = None
    furniture_type: Annotated[FurnitureType | None, _lenient_enum(FurnitureType)] = None
    dimensions: LongSlot = None
    length_m: Annotated[float | None, BeforeValidator(_length_m)] = None
    price_class: Annotated[PriceClass | None, _lenient_enum(PriceClass)] = None
    style: ShortSlot = None
    budget: ShortSlot = None
    city: ShortSlot = None
    deadline: ShortSlot = None
    phone: Annotated[str | None, BeforeValidator(normalize_phone)] = None
    comment: LongSlot = None
    business_role: ShortSlot = None
    lead_sources: ShortSlot = None

    def filled(self) -> dict[str, str]:
        return {k: str(v) for k, v in self.model_dump(exclude_none=True).items()}


class LLMReply(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reply: Annotated[str, _clean_text(REPLY_MAX_LEN), Field(min_length=1)]
    slots: LeadSlotsPatch = Field(default_factory=LeadSlotsPatch)
    ready: bool = False
    # Catalog category to show the client after the reply; None -> no catalog card.
    catalog: Annotated[CatalogCategory | None, _lenient_enum(CatalogCategory)] = None
