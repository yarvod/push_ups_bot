import json

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import ReplyParameters
from arq import Retry
from arq.connections import ArqRedis, RedisSettings
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from pullups_bot.application.ports import ChatModel
from pullups_bot.bootstrap import create_container
from pullups_bot.config import Settings
from pullups_bot.infrastructure.banter import BANTER_QUEUE
from pullups_bot.presentation.banter import THINKING_FRAMES, generate_answer


async def startup(ctx: dict) -> None:
    settings = Settings()
    container = create_container(settings)
    ctx.update(
        container=container,
        settings=settings,
        bot=await container.get(Bot),
        model=await container.get(ChatModel),
        redis=await container.get(ArqRedis),
    )


async def shutdown(ctx: dict) -> None:
    await ctx["container"].close()


async def answer_reply(ctx: dict, chat_id: int, source_id: int, text: str, reply_text: str) -> None:
    redis = ctx["redis"]
    bot = ctx["bot"]
    key = f"pushups:banter:reply:{chat_id}:{source_id}"
    raw = await redis.get(key)
    state = json.loads(raw) if raw else {}
    if state.get("done"):
        return
    try:
        if not state:
            thinking = await bot.send_message(
                chat_id,
                THINKING_FRAMES[0],
                reply_parameters=ReplyParameters(
                    message_id=source_id, allow_sending_without_reply=True
                ),
                parse_mode=None,
            )
            state = {"message_id": thinking.message_id}
            await redis.set(key, json.dumps(state), ex=86400)
        if "answer" not in state:
            state["answer"] = await generate_answer(
                bot,
                ctx["model"],
                chat_id,
                state["message_id"],
                text,
                reply_text,
                ctx["settings"].llm_timeout_seconds,
            )
            # Persist before editing so a transient Telegram failure reuses the result.
            await redis.set(key, json.dumps(state), ex=86400)
        try:
            await bot.edit_message_text(
                state["answer"],
                chat_id=chat_id,
                message_id=state["message_id"],
                parse_mode=None,
            )
        except TelegramBadRequest as exc:
            if not any(
                reason in exc.message.casefold()
                for reason in ("message is not modified", "message to edit not found")
            ):
                raise
        state["done"] = True
        await redis.set(key, json.dumps(state), ex=86400)
    except TelegramRetryAfter as exc:
        raise Retry(defer=exc.retry_after + 1) from None
    except (TelegramNetworkError, TelegramServerError, RedisConnectionError, RedisTimeoutError):
        raise Retry(defer=5) from None


_settings = Settings()


class BanterWorkerSettings:
    functions = [answer_reply]
    queue_name = BANTER_QUEUE
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    on_startup = startup
    on_shutdown = shutdown
    # These jobs can animate concurrently, but LocalChatModel serializes inference.
    max_jobs = 4
    job_timeout = 75
    max_tries = 5
    keep_result = 86400
    health_check_interval = 30
    health_check_key = "pushups:banter:health"
    log_results = False
