import asyncio
import re
from datetime import timedelta

from aiohttp import ClientError, ClientSession
from arq.connections import ArqRedis

from pullups_bot.config import Settings

BANTER_QUEUE = "pushups:banter"
SYSTEM_PROMPT = (
    "Ты остроумный приятель. На каждое сообщение отвечай готовой доброй шуткой "
    "по-русски в одном коротком предложении. Без вступлений."
)
MAX_INPUT_CHARS = 240
MAX_QUOTE_CHARS = 80


class ModelUnavailable(Exception):
    pass


def clean_answer(
    answer: str, finish_reason: str | None = None, source: str = "", quote: str = ""
) -> str:
    # A preamble followed by a blank line used to hide the actual joke.
    answer = answer.strip()
    answer = re.sub(
        r"^(?:конечно[!.,:]?\s*)?(?:(?:вот\s+)?(?:шутка|анекдот)(?:\s+для\s+тебя)?\s*:\s*)?",
        "",
        answer,
        flags=re.IGNORECASE,
    ).strip()
    boilerplate = (
        "не могу это сделать",
        "не могу ответить",
        "не могу придумать",
        "не могу шутить",
        "не могу продолжить",
        "я языковая модель",
        "как языковая модель",
        "чем могу помочь",
        "как я могу помочь",
        "если у вас есть другой вопрос",
        "ошибся в предыдущих ответах",
    )
    if (
        not answer
        or answer.endswith(":")
        or finish_reason == "length"
        or any(phrase in answer.casefold() for phrase in boilerplate)
        or answer.casefold() in ("шутка не удалась.", "увы, шутка не удалась.")
    ):
        raise ModelUnavailable("Unusable model response")
    answer_words = set(re.findall(r"\w+", answer.casefold()))
    for original in (source[:MAX_INPUT_CHARS], quote[:MAX_QUOTE_CHARS]):
        original_words = set(re.findall(r"\w+", original.casefold()))
        if len(original_words) >= 3 and answer_words == original_words:
            raise ModelUnavailable("Model echoed the input")
    return answer[:1000]


def chat_messages(text: str, reply_text: str) -> list[dict[str, str]]:
    try:
        quote = clean_answer(reply_text)
    except ModelUnavailable:
        # Do not teach the small model to repeat its previous boilerplate.
        quote = "Шутка не удалась."
    content = f"Бот: {quote[:MAX_QUOTE_CHARS]}\nРеплика: {text[:MAX_INPUT_CHARS]}"
    if re.search(r"шут|анекдот|прикол", text, flags=re.IGNORECASE):
        # A direct joke request needs no old quote for this tiny model to echo.
        content = text[:MAX_INPUT_CHARS]
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Ну пошути тогда"},
        {"role": "assistant", "content": "Мой пресс как Wi-Fi: все верят, что он есть."},
        {"role": "user", "content": "Бот: Шутка не удалась.\nРеплика: Увы"},
        {"role": "assistant", "content": "Шутка ушла в прогул без уважительной причины."},
        {
            "role": "user",
            "content": content,
        },
    ]


class RedisBanterQueue:
    def __init__(self, redis: ArqRedis):
        self.redis = redis

    async def enqueue(self, chat_id: int, message_id: int, text: str, reply_text: str) -> None:
        await self.redis.enqueue_job(
            "answer_reply",
            chat_id,
            message_id,
            text[:MAX_INPUT_CHARS],
            reply_text[:MAX_QUOTE_CHARS],
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
                        "messages": chat_messages(text, reply_text),
                        "max_tokens": self.settings.llm_max_tokens,
                        "temperature": 0.4,
                        "top_p": 0.8,
                        "top_k": 20,
                        "min_p": 0,
                        "cache_prompt": True,
                        "chat_template_kwargs": {"enable_thinking": False},
                        "stream": False,
                    },
                ) as response,
            ):
                response.raise_for_status()
                payload = await response.json()
            choice = payload["choices"][0]
            answer = choice["message"]["content"]
            if not isinstance(answer, str):
                raise ModelUnavailable("Empty model response")
            return clean_answer(answer, choice.get("finish_reason"), text, reply_text)
        except (ClientError, TimeoutError, KeyError, IndexError, TypeError, ValueError) as exc:
            # Never log the request, reply, endpoint response or any credentials.
            raise ModelUnavailable(type(exc).__name__) from None
