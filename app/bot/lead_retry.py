import asyncio

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.locks import UserLocks
from app.db.repositories import LeadRepository
from app.services.leads import LeadServiceFactory


class LeadDeliveryRetrier:
    """Background job: re-delivers leads stuck half-way (CRM was down, card failed,
    the bot stopped between qualification and delivery).

    Each lead is processed under the same per-user lock as that user's updates, so it never
    races with a handler that is delivering the very same lead right now.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        services: LeadServiceFactory,
        locks: UserLocks,
        interval_s: float,
    ) -> None:
        self._session_factory = session_factory
        self._services = services
        self._locks = locks
        self._interval_s = interval_s

    async def run_once(self) -> int:
        """One pass over undelivered leads. Returns how many the CRM accepted."""
        async with self._session_factory() as session:
            pending = await LeadRepository(session).list_undelivered()
        if not pending:
            return 0
        logger.info("lead retry started pending={}", len(pending))
        delivered = 0
        for lead_id, tg_id in pending:
            async with self._locks.get(tg_id), self._session_factory() as session:
                with logger.contextualize(lead_id=lead_id, tg_id=tg_id):
                    if await self._services.build(session).submit(lead_id):
                        delivered += 1
        logger.info("lead retry done pending={} delivered={}", len(pending), delivered)
        return delivered

    async def run_forever(self) -> None:
        """First pass right at startup, then every `interval_s`; cancelled on shutdown."""
        while True:
            try:
                await self.run_once()
            except Exception:
                # One bad pass (DB error, bug) must not kill the job for the bot's lifetime.
                logger.exception("lead retry pass failed")
            await asyncio.sleep(self._interval_s)
