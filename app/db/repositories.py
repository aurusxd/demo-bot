from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base
from app.db.models import Lead, LeadMedia, MediaKind, Message, User


class UnitOfWork:
    """Commits the update's session.

    Services commit before slow external calls (LLM, HTTP) so SQLite's write lock is
    never held while waiting: otherwise every other update fails with "database is locked".
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()


class BaseRepository[ModelT: Base]:
    """Common CRUD. The only layer that talks SQLAlchemy; transactions are owned by the caller."""

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, obj_id: int) -> ModelT | None:
        return await self._session.get(self.model, obj_id)

    async def add(self, obj: ModelT) -> ModelT:
        self._session.add(obj)
        await self._session.flush()
        return obj

    async def save(self, obj: ModelT) -> ModelT:
        """Flush pending attribute changes of an already tracked object."""
        await self._session.flush()
        return obj

    async def delete(self, obj: ModelT) -> None:
        await self._session.delete(obj)
        await self._session.flush()


class UserRepository(BaseRepository[User]):
    model = User

    async def upsert(self, tg_id: int, username: str | None) -> User:
        """Atomic insert-or-update by tg_id: safe for concurrent first updates of a new user."""
        stmt = (
            sqlite_insert(User)
            .values(tg_id=tg_id, username=username)
            .on_conflict_do_update(index_elements=[User.tg_id], set_={"username": username})
            .returning(User)
        )
        result = await self._session.scalars(stmt, execution_options={"populate_existing": True})
        return result.one()


class LeadRepository(BaseRepository[Lead]):
    model = Lead

    async def get_latest(self, user_id: int) -> Lead | None:
        """The user's current dialog: the newest lead in any status."""
        stmt = select(Lead).where(Lead.user_id == user_id).order_by(Lead.id.desc()).limit(1)
        return (await self._session.scalars(stmt)).first()


class LeadMediaRepository(BaseRepository[LeadMedia]):
    model = LeadMedia

    async def list_file_ids(self, lead_id: int, kind: MediaKind) -> list[str]:
        """Telegram file ids of a lead's media in the order they were sent."""
        stmt = (
            select(LeadMedia.tg_file_id)
            .where(LeadMedia.lead_id == lead_id, LeadMedia.kind == kind)
            .order_by(LeadMedia.id)
        )
        return list((await self._session.scalars(stmt)).all())


class MessageRepository(BaseRepository[Message]):
    model = Message

    async def get_last(self, lead_id: int, limit: int) -> list[Message]:
        """Last `limit` messages of a lead in chronological order."""
        stmt = (
            select(Message)
            .where(Message.lead_id == lead_id)
            .order_by(Message.id.desc())
            .limit(limit)
        )
        return list(reversed((await self._session.scalars(stmt)).all()))
