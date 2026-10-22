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
