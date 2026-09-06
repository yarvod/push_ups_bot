import logging
from datetime import datetime, timedelta
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramServerError
from aiogram.types import Update
from arq import Retry, cron
from arq.connections import RedisSettings

from pullups_bot.application.ports import Mutex, Repository
from pullups_bot.application.service import ClubService
from pullups_bot.bootstrap import create_container, create_dispatcher
from pullups_bot.config import Settings
from pullups_bot.domain.models import RuleError, Status
from pullups_bot.infrastructure.sheets import SheetsError, SheetsRepository
from pullups_bot.presentation.texts import report

logger = logging.getLogger(__name__)


async def startup(ctx: dict) -> None:
    settings = Settings()
    container = create_container(settings)
    ctx.update(settings=settings, container=container, dispatcher=create_dispatcher(container))
    repository = await container.get(Repository)
    mutex = await container.get(Mutex)
    async with mutex.hold():
        assert isinstance(repository, SheetsRepository)
        await repository.initialize()
    logger.info("Worker ready; Google Sheets connected")


async def shutdown(ctx: dict) -> None:
    await ctx["container"].close()


async def process_update(ctx: dict, payload: dict) -> None:
    bot = await ctx["container"].get(Bot)
    update = Update.model_validate(payload, context={"bot": bot})
    try:
        await ctx["dispatcher"].feed_update(bot, update)
    except RuleError as exc:
        if update.message:
            await update.message.answer(escape(str(exc)))
    except TelegramRetryAfter as exc:
        raise Retry(defer=exc.retry_after + 1) from None
    except (SheetsError, TelegramNetworkError, TelegramServerError) as exc:
        logger.warning("Update delayed: %s", type(exc).__name__)
        raise Retry(defer=min(300, 2 ** min(ctx["job_try"], 8))) from None


async def scheduled_tick(ctx: dict) -> None:
    """Minute-level reconciliation also catches a missed scheduled time after downtime."""
    container = ctx["container"]
    settings = ctx["settings"]
    repository = await container.get(Repository)
    service = await container.get(ClubService)
    bot = await container.get(Bot)
    now = datetime.now(settings.tz)
    chat = await repository.state("chat_id")
    if not chat:
        return
    await service.finalize(now)
    snapshot = await repository.snapshot()
    today = snapshot.day(now.date())
    reminder_at = now.replace(
        hour=settings.reminder_time.hour,
        minute=settings.reminder_time.minute,
        second=0,
        microsecond=0,
    )
    summary_at = now.replace(
        hour=settings.summary_time.hour,
        minute=settings.summary_time.minute,
        second=0,
        microsecond=0,
    )
    async with service.mutex.hold():
        if now >= reminder_at and now < reminder_at + timedelta(hours=2):
            if await repository.state("last_reminder") != now.date().isoformat():
                pending = [
                    m
                    for m in snapshot.members
                    if today.statuses[m.name] in {Status.PENDING, Status.MISSED}
                ]
                if pending:
                    tags = []
                    for member in pending:
                        uid = await repository.state(f"member:{member.name}")
                        tags.append(
                            f'<a href="tg://user?id={int(uid)}">{escape(member.name)}</a>'
                            if uid
                            else "@" + escape(member.username)
                        )
                    await bot.send_message(
                        int(chat),
                        " ".join(tags) + "\nПодъём, банда! Пол уже соскучился по вашим мордам 💪\n"
                        f"Видосов пока нет. До {settings.deadline_time:%H:%M} ещё можно "
                        "спасти грудь и пивной бюджет. Погнали, хули лежим 😏",
                    )
                await repository.save_state("last_reminder", now.date().isoformat())
        if now >= summary_at:
            if await repository.state("last_summary") != now.date().isoformat():
                await bot.send_message(int(chat), report(snapshot, now, settings))
                await repository.save_state("last_summary", now.date().isoformat())


_settings = Settings()


class WorkerSettings:
    functions = [process_update]
    cron_jobs = [cron(scheduled_tick, minute=None, second=5, run_at_startup=True, max_tries=10)]
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    timezone = _settings.tz
    on_startup = startup
    on_shutdown = shutdown
    # One consumer preserves Telegram message order; the Redis mutex also protects deployments.
    max_jobs = 1
    job_timeout = 110
    max_tries = 1000
    keep_result = 7 * 86400
    health_check_interval = 30
    health_check_key = "pushups:worker:health"
    log_results = False
