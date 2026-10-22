from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Date, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LeadStatus(StrEnum):
    DRAFT = "draft"
    QUALIFIED = "qualified"
    SENT = "sent"


class FurnitureType(StrEnum):
    KITCHEN = "kitchen"
    WARDROBE = "wardrobe"
    WALK_IN = "walk_in"


class CatalogCategory(StrEnum):
    KITCHEN = "kitchen"
    WARDROBE = "wardrobe"
    WALK_IN = "walk_in"
    INTERIOR = "interior"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageKind(StrEnum):
    TEXT = "text"
    VOICE = "voice"
    PHOTO = "photo"


class MediaKind(StrEnum):
    PHOTO = "photo"
    VOICE = "voice"


class TryOnStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


def _str_enum(enum_cls: type[StrEnum]) -> Enum:
    """Store enum *values* as VARCHAR (portable, no native enum type)."""
    return Enum(enum_cls, native_enum=False, length=16, values_callable=_enum_values)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created_at()
    tryon_used_today: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tryon_reset_date: Mapped[date | None] = mapped_column(Date)


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[LeadStatus] = mapped_column(
        _str_enum(LeadStatus), default=LeadStatus.DRAFT, server_default=LeadStatus.DRAFT.value
    )
    name: Mapped[str | None] = mapped_column(String(128))
    furniture_type: Mapped[FurnitureType | None] = mapped_column(_str_enum(FurnitureType))
    dimensions: Mapped[str | None] = mapped_column(Text)
    style: Mapped[str | None] = mapped_column(String(128))
    budget: Mapped[str | None] = mapped_column(String(128))
    city: Mapped[str | None] = mapped_column(String(128))
    deadline: Mapped[str | None] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(32))
    comment: Mapped[str | None] = mapped_column(Text)
    # About the demo visitor as a furniture business (B2B), not about the order.
    business_role: Mapped[str | None] = mapped_column(String(128))
    lead_sources: Mapped[str | None] = mapped_column(String(255))
    estimate_min: Mapped[int | None] = mapped_column(Integer)
    estimate_max: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created_at()
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    role: Mapped[MessageRole] = mapped_column(_str_enum(MessageRole))
    content: Mapped[str] = mapped_column(Text)
    kind: Mapped[MessageKind] = mapped_column(_str_enum(MessageKind))
    created_at: Mapped[datetime] = _created_at()


class LeadMedia(Base):
    __tablename__ = "lead_media"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    tg_file_id: Mapped[str] = mapped_column(String(255))
    kind: Mapped[MediaKind] = mapped_column(_str_enum(MediaKind))


class CatalogItem(Base):
    __tablename__ = "catalog_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[CatalogCategory] = mapped_column(_str_enum(CatalogCategory), index=True)
    title: Mapped[str] = mapped_column(String(255))
    style: Mapped[str | None] = mapped_column(String(128))
    image_path: Mapped[str] = mapped_column(String(512))
    tg_file_id: Mapped[str | None] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(String(1024))


class TryOnRequest(Base):
    __tablename__ = "tryon_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    catalog_item_id: Mapped[int] = mapped_column(ForeignKey("catalog_items.id", ondelete="CASCADE"))
    status: Mapped[TryOnStatus] = mapped_column(
        _str_enum(TryOnStatus),
        default=TryOnStatus.PENDING,
        server_default=TryOnStatus.PENDING.value,
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
