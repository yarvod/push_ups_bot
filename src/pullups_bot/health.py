import asyncio
import sys

from redis.asyncio import Redis

from pullups_bot.config import Settings


async def check() -> bool:
    redis = Redis.from_url(Settings().redis_url)
    try:
        role = "banter" if "banter" in sys.argv else "worker" if "worker" in sys.argv else "poller"
        key = f"pushups:{role}:health"
        return bool(await redis.exists(key))
    finally:
        await redis.aclose()


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(check()) else 1)
