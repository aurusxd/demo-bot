from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base
from app.db.models import User


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
