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

    async def delete(self, user: User) -> None:
        """/delete_me: the user and everything of theirs (leads, messages, media, try-ons).

        Children go by ON DELETE CASCADE. Cards already sent to managers and leads already
        accepted by the CRM live outside the bot and are not touched.
        """
        user_id = user.id
        await self._users.delete(user)
        await self._uow.commit()
        logger.info("user deleted user_id={}", user_id)
