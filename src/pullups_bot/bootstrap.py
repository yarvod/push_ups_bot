from collections.abc import AsyncIterator

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from arq.connections import ArqRedis, RedisSettings, create_pool
from dishka import Provider, Scope, from_context, make_async_container, provide
from dishka.integrations.aiogram import inject_router, setup_dishka

from pullups_bot.application.ports import ManualPromptStore, Mutex, Repository
from pullups_bot.application.service import ClubService
from pullups_bot.config import Settings
from pullups_bot.infrastructure.locking import RedisMutex
from pullups_bot.infrastructure.prompts import RedisManualPromptStore
from pullups_bot.infrastructure.sheets import SheetsRepository
from pullups_bot.presentation.handlers import create_router


class AppProvider(Provider):
    scope = Scope.APP
    settings = from_context(provides=Settings)

    @provide
    async def redis(self, settings: Settings) -> AsyncIterator[ArqRedis]:
        redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        yield redis
        await redis.aclose()

    @provide
    def mutex(self, redis: ArqRedis, settings: Settings) -> Mutex:
        return RedisMutex(redis, settings.spreadsheet_id)

    @provide
    def prompts(self, redis: ArqRedis) -> ManualPromptStore:
        return RedisManualPromptStore(redis)

    @provide
    async def repository(self, settings: Settings) -> AsyncIterator[Repository]:
        repository = SheetsRepository(settings)
        yield repository
        await repository.close()

    @provide
    def service(self, repository: Repository, mutex: Mutex, settings: Settings) -> ClubService:
        return ClubService(repository, mutex, settings.owner_id, settings.deadline_time)

    @provide
    async def bot(self, settings: Settings) -> AsyncIterator[Bot]:
        bot = Bot(
            settings.token.get_secret_value(),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        yield bot
        await bot.session.close()


def create_container(settings: Settings):
    return make_async_container(AppProvider(), context={Settings: settings})


def create_dispatcher(container) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router())
    setup_dishka(container=container, router=dispatcher)
    # ARQ feeds updates directly, so aiogram's polling startup hooks are not emitted.
    inject_router(dispatcher)
    return dispatcher
