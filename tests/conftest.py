from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aiogram import Bot
from aiogram.methods import TelegramMethod
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db import models  # noqa: F401  # registers models in Base.metadata
from app.db.base import Base, create_engine, create_session_factory


async def _create_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_engine("sqlite+aiosqlite://")
    await _create_schema(engine)
    async with create_session_factory(engine)() as db_session:
        yield db_session
    await engine.dispose()


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine("sqlite+aiosqlite://")
    await _create_schema(engine)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
async def file_session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Real file-based SQLite: unlike in-memory StaticPool it has real write locks."""
    engine = create_engine(f"sqlite+aiosqlite:///{(tmp_path / 'app.db').as_posix()}")
    await _create_schema(engine)
    yield create_session_factory(engine)
    await engine.dispose()


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[TelegramMethod[Any]]:
    """Captures every Bot API call instead of sending it."""
    calls: list[TelegramMethod[Any]] = []

    async def fake_call(self: Bot, method: TelegramMethod[Any], request_timeout: int | None = None):
        calls.append(method)
        return True

    monkeypatch.setattr(Bot, "__call__", fake_call)
    return calls
