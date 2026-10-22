from typing import Any
from unittest.mock import AsyncMock

from aiogram.types import User as TgUser
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.middlewares.user import UserMiddleware
from app.db.models import User
from app.db.repositories import UnitOfWork, UserRepository
from app.services.users import UserService


async def count_users(session: AsyncSession) -> int:
    return (await session.execute(select(func.count()).select_from(User))).scalar_one()


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
    handler.assert_awaited_once()


async def test_user_middleware_skips_updates_without_sender(session: AsyncSession) -> None:
    handler = AsyncMock(return_value="ok")
    data: dict[str, Any] = {"session": session}

    await UserMiddleware()(handler, AsyncMock(), data)

    assert "user" not in data
    assert await count_users(session) == 0
