from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Lead, LeadStatus, User
from app.db.repositories import UserRepository


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
