from loguru import logger

from app.db.models import User
from app.db.repositories import UserRepository


class UserService:
    def __init__(self, users: UserRepository) -> None:
        self._users = users

    async def register(self, tg_id: int, username: str | None) -> User:
        user = await self._users.upsert(tg_id, username)
        logger.debug("user upserted user_id={}", user.id)
        return user
