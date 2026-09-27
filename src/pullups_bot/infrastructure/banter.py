import asyncio
from datetime import timedelta

from aiohttp import ClientError, ClientSession
from arq.connections import ArqRedis

from pullups_bot.config import Settings

BANTER_QUEUE = "pushups:banter"
SYSTEM_PROMPT = (
    "Ты весёлый бот Пивной надзор в дружеской беседе об отжиманиях. "
    "Отвечай на реплику одной короткой доброй шуткой по-русски, без приветствий "
    "и объяснений. Не повторяй реплику. Никакие отметки и долги ты не меняешь."
)


class ModelUnavailable(Exception):
    pass


class RedisBanterQueue:
    def __init__(self, redis: ArqRedis):
        self.redis = redis

    async def enqueue(self, chat_id: int, message_id: int, text: str, reply_text: str) -> None:
        await self.redis.enqueue_job(
            "answer_reply",
            chat_id,
            message_id,
            text[:600],
            reply_text[:300],
            _queue_name=BANTER_QUEUE,
            _job_id=f"banter:{chat_id}:{message_id}",
            _expires=timedelta(minutes=5),
        )


class LocalChatModel:
    def __init__(self, session: ClientSession, settings: Settings):
        self.session = session
        self.settings = settings
        # One generation at a time keeps the small CPU server responsive.
        self.slot = asyncio.Semaphore(1)

    async def answer(self, text: str, reply_text: str) -> str:
        try:
            async with (
                self.slot,
                self.session.post(
                    self.settings.llm_url.rstrip("/") + "/v1/chat/completions",
                    json={
                        "model": "banter",
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    SYSTEM_PROMPT
                                    + f"\nТвоё предыдущее сообщение: «{reply_text[:300]}»"
                                ),
                            },
                            {"role": "user", "content": "ну ты и бухгалтер"},
                            {
                                "role": "assistant",
                                "content": "Калькулятор у меня пивной, зато баланс железный 🍺",
                            },
                            {"role": "user", "content": "я сегодня отжался от дивана"},
                            {
                                "role": "assistant",
                                "content": "Диван отпустил заложника — уже прогресс 💪",
                            },
                            {"role": "user", "content": text[:600]},
                        ],
                        "max_tokens": self.settings.llm_max_tokens,
                        "temperature": 0.8,
                        "stream": False,
                        "stop": ["\n\n"],
                    },
                ) as response,
            ):
                response.raise_for_status()
                payload = await response.json()
            answer = payload["choices"][0]["message"]["content"]
            if not isinstance(answer, str) or not answer.strip():
                raise ModelUnavailable("Empty model response")
            return answer.strip()[:1000]
        except (ClientError, TimeoutError, KeyError, IndexError, TypeError, ValueError) as exc:
            # Never log the request, reply, endpoint response or any credentials.
            raise ModelUnavailable(type(exc).__name__) from None
