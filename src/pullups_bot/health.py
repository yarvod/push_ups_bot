import asyncio
import sys

from redis.asyncio import Redis

from pullups_bot.config import Settings


async def check() -> bool:
    redis = Redis.from_url(Settings().redis_url)
    try:
        key = "pushups:worker:health" if "worker" in sys.argv else "pushups:poller:health"
        return bool(await redis.exists(key))
    finally:
        await redis.aclose()


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(check()) else 1)
