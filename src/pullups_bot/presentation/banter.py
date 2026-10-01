import asyncio
import logging
import zlib
from contextlib import suppress

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from pullups_bot.application.ports import ChatModel
from pullups_bot.infrastructure.banter import ModelUnavailable

logger = logging.getLogger(__name__)
THINKING_FRAMES = ("🧠 Думаю ⠋", "🧠 Думаю ⠙", "🧠 Думаю ⠹", "🧠 Думаю ⠸")
ANIMATION_INTERVAL = 2.5
FALLBACK_JOKES = (
    "Диван опять победил. У него, сука, преимущество домашнего поля.",
    "Мой пресс ушёл в отпуск и даже открытку не прислал.",
    "План был мощный. Потом я прилёг обсудить его с диваном.",
    "Сегодня я отжался от работы. Техника была безупречная.",
    "Пивной надзор докладывает: мотивация снова скрылась с места тренировки.",
)


def fallback_answer(text: str, message_id: int) -> str:
    key = f"{message_id}:{text}".encode()
    return FALLBACK_JOKES[zlib.crc32(key) % len(FALLBACK_JOKES)]


async def animate(bot: Bot, chat_id: int, message_id: int) -> None:
    frame = 1
    while True:
        await asyncio.sleep(ANIMATION_INTERVAL)
        try:
            await bot.edit_message_text(
                THINKING_FRAMES[frame % len(THINKING_FRAMES)],
                chat_id=chat_id,
                message_id=message_id,
                parse_mode=None,
            )
        except TelegramAPIError:
            # Animation is optional; an API limit must not interrupt generation.
            return
        frame += 1


async def generate_answer(
    bot: Bot,
    model: ChatModel,
    chat_id: int,
    thinking_id: int,
    text: str,
    reply_text: str,
    timeout_seconds: float,
) -> str:
    animation = asyncio.create_task(animate(bot, chat_id, thinking_id))
    try:
        async with asyncio.timeout(timeout_seconds):
            answer = await model.answer(text, reply_text)
        return answer.strip()[:1000] or fallback_answer(text, thinking_id)
    except (ModelUnavailable, TimeoutError) as exc:
        logger.info("Banter fallback: %s", type(exc).__name__)
        return fallback_answer(text, thinking_id)
    finally:
        animation.cancel()
        with suppress(asyncio.CancelledError):
            await animation
