from loguru import logger

from app.db.models import User
from app.db.repositories import UnitOfWork, UserRepository


class UserService:
    def __init__(self, users: UserRepository, uow: UnitOfWork) -> None:
        self._users = users
        self._uow = uow

    async def register(self, tg_id: int, username: str | None) -> User:
        user = await self._users.upsert(tg_id, username)
        await self._uow.commit()  # never keep the write lock for the rest of the update
        logger.debug("user upserted user_id={}", user.id)
        return user
