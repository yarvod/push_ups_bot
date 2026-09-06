import asyncio
import logging
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramServerError
from arq.connections import ArqRedis

from pullups_bot.bootstrap import create_container
from pullups_bot.config import Settings

logger = logging.getLogger(__name__)


async def main() -> None:
    """Acknowledge Telegram updates only after durable enqueue in Redis."""
    container = create_container(Settings())
    try:
        bot = await container.get(Bot)
        redis = await container.get(ArqRedis)
        webhook = await bot.get_webhook_info()
        if webhook.url:
            raise RuntimeError(
                "Existing webhook detected. Remove it explicitly before long polling."
            )
        offset = None
        allowed = ["message", "my_chat_member"]
        while True:
            try:
                await redis.set("pushups:poller:health", "ok", ex=90)
                updates = await bot.get_updates(offset=offset, timeout=30, allowed_updates=allowed)
                for update in updates:
                    await redis.enqueue_job(
                        "process_update",
                        update.model_dump(mode="json", exclude_none=True),
                        _job_id=f"telegram:{bot.id}:{update.update_id}",
                        _expires=timedelta(days=7),
                    )
                    # Next getUpdates call is Telegram's acknowledgement. enqueue failure exits
                    # the process without advancing the server offset; duplicates are idempotent.
                    offset = update.update_id + 1
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after + 1)
            except (TelegramNetworkError, TelegramServerError):
                logger.warning("Telegram unavailable, reconnecting")
                await asyncio.sleep(5)
    finally:
        await container.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(main())
