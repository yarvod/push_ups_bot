import json
from dataclasses import asdict

from arq.connections import ArqRedis

from pullups_bot.application.manual import ManualPrompt


class RedisManualPromptStore:
    def __init__(self, redis: ArqRedis):
        self.redis = redis

    async def save(self, chat_id: int, message_id: int, prompt: ManualPrompt) -> None:
        await self.redis.set(
            f"pushups:prompt:{chat_id}:{message_id}", json.dumps(asdict(prompt)), ex=86400
        )

    async def get(self, chat_id: int, message_id: int) -> ManualPrompt | None:
        raw = await self.redis.get(f"pushups:prompt:{chat_id}:{message_id}")
        return ManualPrompt(**json.loads(raw)) if raw else None

    async def delete(self, chat_id: int, message_id: int) -> None:
        await self.redis.delete(f"pushups:prompt:{chat_id}:{message_id}")
