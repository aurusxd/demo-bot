import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Lead, LeadStatus, User
from app.db.repositories import LeadRepository, UserRepository


async def test_add_get_delete_user(session: AsyncSession) -> None:
    repo = UserRepository(session)

    user = await repo.add(User(tg_id=111, username="demo"))
    loaded = await repo.get(user.id)

    assert loaded is not None
    assert loaded.tg_id == 111
    assert loaded.tryon_used_today == 0

    await repo.delete(user)
    assert await repo.get(user.id) is None


async def test_lead_defaults_and_cascade_delete(session: AsyncSession) -> None:
    repo = UserRepository(session)
    user = await repo.add(User(tg_id=222))
    session.add(Lead(user_id=user.id))
    await session.flush()

    lead = (await session.execute(select(Lead))).scalar_one()
    assert lead.status is LeadStatus.DRAFT

    await repo.delete(user)
    session.expunge_all()
    leads_left = (await session.execute(select(func.count()).select_from(Lead))).scalar_one()
    assert leads_left == 0


async def test_get_missing_returns_none(session: AsyncSession) -> None:
    assert await UserRepository(session).get(999) is None


async def test_sql_errors_do_not_expose_parameters(session: AsyncSession) -> None:
    """Review #3: SQL errors end up in logs, they must not carry user data."""
    session.add(User(tg_id=777, username="first"))
    await session.flush()
    session.add(User(tg_id=777, username="secret_pii_value"))

    with pytest.raises(IntegrityError) as exc_info:
        await session.flush()

    assert "secret_pii_value" not in str(exc_info.value)
    assert "hidden due to hide_parameters" in str(exc_info.value)


async def test_get_latest_returns_newest_lead_in_any_status(session: AsyncSession) -> None:
    user = await UserRepository(session).add(User(tg_id=1))
    session.add(Lead(user_id=user.id, status=LeadStatus.DRAFT))
    session.add(Lead(user_id=user.id, status=LeadStatus.QUALIFIED))
    await session.flush()

    latest = await LeadRepository(session).get_latest(user.id)

    assert latest is not None
    assert latest.status is LeadStatus.QUALIFIED
    assert await LeadRepository(session).get_latest(999) is None
