import asyncio
from weakref import WeakValueDictionary


class UserLocks:
    """Per-user asyncio locks shared by the update pipeline and background jobs.

    In-process locks are enough: the bot runs as a single process. Weak values: a lock
    disappears once nobody holds or waits for it.
    """

    def __init__(self) -> None:
        self._locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()

    def get(self, tg_id: int) -> asyncio.Lock:
        lock = self._locks.get(tg_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[tg_id] = lock
        return lock
