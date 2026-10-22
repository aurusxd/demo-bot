import asyncio

from alembic import context
from sqlalchemy.engine import Connection

from app.config import get_settings
from app.db import models  # noqa: F401  # registers models in Base.metadata
from app.db.base import Base, create_engine

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    # Tests pass an explicit url; otherwise use app settings (DATABASE_URL).
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        render_as_batch=True,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_sync(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_engine(_database_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run_sync)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
