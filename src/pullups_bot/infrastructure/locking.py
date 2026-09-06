from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.asyncio import Redis


class RedisMutex:
    def __init__(self, redis: Redis, spreadsheet_id: str):
        self.redis = redis
        self.key = f"pushups:write:{spreadsheet_id}"

    @asynccontextmanager
    async def hold(self) -> AsyncIterator[None]:
        # A watchdog renews the lease while Google requests are in flight.
        import asyncio

        lock = self.redis.lock(self.key, timeout=120, blocking_timeout=30)
        async with lock:

            async def renew() -> None:
                while True:
                    await asyncio.sleep(30)
                    await lock.extend(120, replace_ttl=True)

            task = asyncio.create_task(renew())
            try:
                yield
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
