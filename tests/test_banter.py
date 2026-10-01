import asyncio
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import EditMessageText
from aiohttp import ClientSession, web
from arq import Retry

from pullups_bot.banter_worker import answer_reply
from pullups_bot.infrastructure.banter import (
    MAX_INPUT_CHARS,
    MAX_QUOTE_CHARS,
    LocalChatModel,
    ModelUnavailable,
    RedisBanterQueue,
    chat_messages,
    clean_answer,
)
from pullups_bot.presentation import banter


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.jobs = []

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, **kwargs):
        self.values[key] = value

    async def enqueue_job(self, *args, **kwargs):
        self.jobs.append((args, kwargs))


@pytest.mark.parametrize(
    "answer",
    [
        "Конечно, вот шутка для тебя:",
        "Вот шутка:",
        "Кажется, я ошибся в предыдущих ответах. Если у вас есть другой вопрос, задайте его.",
        "Я не могу это сделать.",
        "Извините, но я не могу продолжить этот разговор в таком формате.",
    ],
)
def test_empty_preambles_and_model_excuses_are_unavailable(answer):
    with pytest.raises(ModelUnavailable):
        clean_answer(answer)


def test_preamble_is_removed_without_losing_the_joke():
    assert clean_answer("Конечно, вот шутка для тебя:\n\nМой пресс в отпуске.") == (
        "Мой пресс в отпуске."
    )


def test_truncated_generation_is_unavailable():
    with pytest.raises(ModelUnavailable):
        clean_answer("Мой пресс как зарплата, потому что", "length")


def test_complete_joke_is_saved_when_generation_trails_off():
    assert clean_answer("Мой пресс ушёл в отпуск. А потом", "length") == (
        "Мой пресс ушёл в отпуск."
    )


def test_model_repeating_the_user_instead_of_answering_is_unavailable():
    with pytest.raises(ModelUnavailable):
        clean_answer("Диван сегодня победил меня.", source="Сегодня диван меня победил")


def test_model_prompt_contains_only_the_instruction_and_current_reply():
    messages = chat_messages("Ну пошути тогда")
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "матом" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "Ну пошути тогда"}


class RecordingBot:
    def __init__(self):
        self.sent = []
        self.edits = []
        self.animated = asyncio.Event()
        self.fail_final = False

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return SimpleNamespace(message_id=500)

    async def edit_message_text(self, text, **kwargs):
        if self.fail_final and text not in banter.THINKING_FRAMES:
            self.fail_final = False
            raise TelegramNetworkError(method=EditMessageText(text=text), message="offline")
        self.edits.append((text, kwargs))
        if text in banter.THINKING_FRAMES:
            self.animated.set()


class FakeModel:
    def __init__(self, answer="Диван тебя сегодня потерял 😏", error=None, wait=None):
        self.result = answer
        self.error = error
        self.wait = wait
        self.calls = 0

    async def answer(self, text, reply_text):
        self.calls += 1
        if self.wait:
            await self.wait.wait()
        if self.error:
            raise self.error
        return self.result


def context(bot, model, timeout=1):
    return {
        "bot": bot,
        "model": model,
        "redis": MemoryRedis(),
        "settings": SimpleNamespace(llm_timeout_seconds=timeout),
    }


async def test_thinking_animates_then_same_message_is_replaced(monkeypatch):
    monkeypatch.setattr(banter, "ANIMATION_INTERVAL", 0.001)
    bot = RecordingBot()
    ctx = context(bot, FakeModel(wait=bot.animated))
    await answer_reply(ctx, -42, 10, "привет", "цитата")
    assert len(bot.sent) == 1
    assert bot.sent[0][1] == banter.THINKING_FRAMES[0]
    assert bot.sent[0][2]["reply_parameters"].message_id == 10
    assert bot.edits[0][0] in banter.THINKING_FRAMES
    assert bot.edits[-1][0] == "Диван тебя сегодня потерял 😏"
    assert all(edit[1]["message_id"] == 500 for edit in bot.edits)
    assert bot.edits[-1][1]["parse_mode"] is None
    count = len(bot.edits)
    await asyncio.sleep(0.005)
    assert len(bot.edits) == count
    await answer_reply(ctx, -42, 10, "привет", "цитата")
    assert len(bot.sent) == 1 and len(bot.edits) == count


@pytest.mark.parametrize("failure", ["error", "empty", "timeout"])
async def test_model_failure_replaces_thinking_with_fallback(failure):
    bot = RecordingBot()
    model = FakeModel(
        answer="" if failure == "empty" else "ok",
        error=ModelUnavailable() if failure == "error" else None,
        wait=asyncio.Event() if failure == "timeout" else None,
    )
    await answer_reply(context(bot, model, timeout=0.01), -42, 10, "hi", "bot")
    assert bot.edits[-1][0] == banter.fallback_answer("hi", 500)
    assert "не смог" not in bot.edits[-1][0]
    assert len(bot.sent) == 1


async def test_telegram_retry_reuses_message_and_generated_answer():
    bot = RecordingBot()
    bot.fail_final = True
    model = FakeModel(answer="<b>Это обычный текст, не HTML</b>")
    ctx = context(bot, model)
    with pytest.raises(Retry):
        await answer_reply(ctx, -42, 10, "hi", "bot")
    await answer_reply(ctx, -42, 10, "hi", "bot")
    assert model.calls == 1 and len(bot.sent) == 1
    assert bot.edits[-1][0] == model.result
    assert bot.edits[-1][1]["parse_mode"] is None


async def test_queue_deduplicates_source_and_limits_model_input():
    redis = MemoryRedis()
    await RedisBanterQueue(redis).enqueue(-42, 10, "a" * 2000, "b" * 2000)
    args, kwargs = redis.jobs[0]
    assert args[:3] == ("answer_reply", -42, 10)
    assert len(args[3]) == MAX_INPUT_CHARS and len(args[4]) == MAX_QUOTE_CHARS
    assert kwargs["_queue_name"] == "pushups:banter"
    assert kwargs["_job_id"] == "banter:-42:10"


@pytest.mark.parametrize("payload", [{"choices": []}, {"choices": [{"message": {"content": ""}}]}])
async def test_malformed_model_response_is_unavailable(settings, payload):
    async def reply(request):
        data = await request.json()
        assert data["messages"][0]["role"] == "system"
        assert len(data["messages"]) == 2
        assert data["stream"] is False
        assert data["chat_template_kwargs"] == {"enable_thinking": False}
        assert data["cache_prompt"] is True
        assert data["temperature"] == 0.8
        assert data["presence_penalty"] == 1.0
        assert "stop" not in data
        return web.json_response(payload)

    app = web.Application()
    app.router.add_post("/v1/chat/completions", reply)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    try:
        async with ClientSession() as session:
            configured = settings.model_copy(update={"llm_url": f"http://127.0.0.1:{port}"})
            with pytest.raises(ModelUnavailable):
                await LocalChatModel(session, configured).answer("привет", "цитата")
    finally:
        await runner.cleanup()


async def test_bad_joke_does_not_start_another_slow_generation(settings):
    requests = []

    async def reply(request):
        requests.append(await request.json())
        content = "Конечно, вот шутка для тебя:"
        return web.json_response(
            {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
        )

    app = web.Application()
    app.router.add_post("/v1/chat/completions", reply)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    try:
        async with ClientSession() as session:
            configured = settings.model_copy(update={"llm_url": f"http://127.0.0.1:{port}"})
            with pytest.raises(ModelUnavailable):
                await LocalChatModel(session, configured).answer("Ну пошути тогда", "Привет")
        assert len(requests) == 1
    finally:
        await runner.cleanup()
