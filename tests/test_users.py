from typing import Any
from unittest.mock import AsyncMock

from aiogram.types import User as TgUser
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.middlewares.user import UserMiddleware
from app.db.base import Base
from app.db.models import (
    CatalogCategory,
    CatalogItem,
    Lead,
    LeadMedia,
    MediaKind,
    Message,
    MessageKind,
    MessageRole,
    TryOnRequest,
    User,
)
from app.db.repositories import UnitOfWork, UserRepository
from app.services.users import UserService


async def count_rows(session: AsyncSession, model: type[Base]) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def count_users(session: AsyncSession) -> int:
    return await count_rows(session, User)


async def add_user_with_data(session: AsyncSession, tg_id: int, item: CatalogItem) -> User:
    """A user with a lead, a message, a lead photo and a try-on request."""
    user = await UserService(UserRepository(session), UnitOfWork(session)).register(tg_id, None)
    lead = Lead(user_id=user.id, phone="+79123451234")
    session.add(lead)
    await session.flush()
    session.add_all(
        [
            Message(lead_id=lead.id, role=MessageRole.USER, content="кухня", kind=MessageKind.TEXT),
            LeadMedia(lead_id=lead.id, tg_file_id=f"photo-{tg_id}", kind=MediaKind.PHOTO),
            TryOnRequest(user_id=user.id, catalog_item_id=item.id),
        ]
    )
    await session.commit()
    return user


async def test_register_creates_user(session: AsyncSession) -> None:
    user = await UserService(UserRepository(session), UnitOfWork(session)).register(100, "alice")

    assert user.id is not None
    assert user.tg_id == 100
    assert user.username == "alice"
    assert user.tryon_used_today == 0


async def test_register_is_idempotent_and_updates_username(session: AsyncSession) -> None:
    service = UserService(UserRepository(session), UnitOfWork(session))

    first = await service.register(100, "alice")
    second = await service.register(100, "alice_new")

    assert second.id == first.id
    assert second.username == "alice_new"
    assert await count_users(session) == 1


async def test_register_without_username(session: AsyncSession) -> None:
    user = await UserService(UserRepository(session), UnitOfWork(session)).register(200, None)

    assert user.username is None


async def test_user_middleware_puts_user_into_data(session: AsyncSession) -> None:
    handler = AsyncMock(return_value="ok")
    data: dict[str, Any] = {
        "session": session,
        "event_from_user": TgUser(id=300, is_bot=False, first_name="Bob", username="bob"),
    }

    result = await UserMiddleware()(handler, AsyncMock(), data)

    assert result == "ok"
    assert data["user"].tg_id == 300
    assert isinstance(data["users"], UserService)
    handler.assert_awaited_once()


async def test_user_middleware_skips_updates_without_sender(session: AsyncSession) -> None:
    handler = AsyncMock(return_value="ok")
    data: dict[str, Any] = {"session": session}

    await UserMiddleware()(handler, AsyncMock(), data)

    assert "user" not in data
    assert await count_users(session) == 0


async def test_delete_erases_user_and_all_their_data(session: AsyncSession) -> None:
    item = CatalogItem(category=CatalogCategory.KITCHEN, title="Кухня", image_path="k/1.jpg")
    session.add(item)
    await session.flush()
    doomed = await add_user_with_data(session, 100, item)
    await add_user_with_data(session, 200, item)

    await UserService(UserRepository(session), UnitOfWork(session)).delete(doomed)
    session.expunge_all()

    users = (await session.scalars(select(User))).all()
    assert [u.tg_id for u in users] == [200]  # other users are untouched
    for model in (Lead, Message, LeadMedia, TryOnRequest):
        assert await count_rows(session, model) == 1
    assert await count_rows(session, CatalogItem) == 1  # the catalog is not the user's data
