import re
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app.db.models import FurnitureType, PriceClass

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


def _furniture_type(value: object) -> FurnitureType | None:
    try:
        return FurnitureType(str(value).strip().lower()) if value else None
    except ValueError:
        return None


def _price_class(value: object) -> PriceClass | None:
    try:
        return PriceClass(str(value).strip().lower()) if value else None
    except ValueError:
        return None


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
    furniture_type: Annotated[FurnitureType | None, BeforeValidator(_furniture_type)] = None
    dimensions: LongSlot = None
    length_m: Annotated[float | None, BeforeValidator(_length_m)] = None
    price_class: Annotated[PriceClass | None, BeforeValidator(_price_class)] = None
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
