from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.db.models import FurnitureType, PriceClass


class LeadEvent(StrEnum):
    CREATED = "lead.created"  # first delivery of a qualified lead
    UPDATED = "lead.updated"  # the client added data or photos after delivery


class LeadPayload(BaseModel):
    """A qualified lead as it leaves the bot: CRM webhook body and manager card source."""

    model_config = ConfigDict(frozen=True)

    event: LeadEvent
    lead_id: int
    tg_id: int
    tg_username: str | None
    created_at: datetime
    name: str | None
    furniture_type: FurnitureType | None
    dimensions: str | None
    length_m: float | None
    price_class: PriceClass | None
    style: str | None
    budget: str | None
    city: str | None
    deadline: str | None
    phone: str | None
    comment: str | None
    business_role: str | None
    lead_sources: str | None
    estimate_min: int | None
    estimate_max: int | None
    currency: str
    photo_file_ids: list[str]

    def filled_fields(self) -> list[str]:
        """Names of non-empty fields: safe to log (no values)."""
        skip = {"event"}
        return [
            name
            for name, value in self.model_dump().items()
            if name not in skip and value not in (None, [])
        ]
