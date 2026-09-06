from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeDefault

COMMANDS = (
    BotCommand(command="today", description="Кто сегодня отжался"),
    BotCommand(command="stats", description="Статистика по участникам"),
    BotCommand(command="debts", description="Кто кому должен пива"),
    BotCommand(command="done", description="Отметить свои отжимания"),
    BotCommand(command="miss", description="Отметить свой пропуск"),
    BotCommand(command="sheet", description="Открыть таблицу"),
    BotCommand(command="help", description="Правила и помощь"),
    BotCommand(command="setup", description="Подключить беседу — только Ярик"),
    BotCommand(command="excuse", description="Подтвердить уважительную причину — только Ярик"),
    BotCommand(command="bind", description="Привязать Telegram ID участника — только Ярик"),
    BotCommand(command="unbind", description="Отключить беседу — только Ярик"),
)


async def register_commands(bot: Bot) -> None:
    """Publish Telegram's slash menu; authorization stays in the command handlers."""
    for language in ("", "ru"):
        await bot.set_my_commands(
            commands=list(COMMANDS), scope=BotCommandScopeDefault(), language_code=language
        )
