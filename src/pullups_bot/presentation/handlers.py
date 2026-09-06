import logging
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import ChatMemberUpdated, Message, ReactionTypeEmoji
from dishka.integrations.aiogram import FromDishka

from pullups_bot.application.ports import Repository
from pullups_bot.application.service import ClubService
from pullups_bot.config import Settings
from pullups_bot.domain.models import RuleError, Status, cutoff_at
from pullups_bot.presentation.texts import debts, help_text, report, statistics

logger = logging.getLogger(__name__)


def create_router() -> Router:
    router = Router(name="club")

    @router.my_chat_member()
    async def membership(
        event: ChatMemberUpdated,
        service: FromDishka[ClubService],
        repository: FromDishka[Repository],
        settings: FromDishka[Settings],
        bot: FromDishka[Bot],
    ):
        old = event.old_chat_member.status
        new = event.new_chat_member.status
        if event.chat.type not in {"group", "supergroup", "channel"}:
            return
        was_present = old not in {"left", "kicked"} and (
            old != "restricted" or getattr(event.old_chat_member, "is_member", False)
        )
        is_present = new not in {"left", "kicked"} and (
            new != "restricted" or getattr(event.new_chat_member, "is_member", False)
        )
        if is_present and not was_present:
            if event.from_user.id != settings.owner_id:
                await bot.leave_chat(event.chat.id)
                return
            # /setup explicitly binds the chat after Telegram privacy is configured.
        if not is_present:
            async with service.mutex.hold():
                if await repository.state("chat_id") == str(event.chat.id):
                    await repository.save_state("chat_id", "")

    @router.message(F.migrate_to_chat_id)
    async def migration(
        message: Message, repository: FromDishka[Repository], service: FromDishka[ClubService]
    ):
        async with service.mutex.hold():
            if await repository.state("chat_id") == str(message.chat.id):
                await repository.save_state("chat_id", str(message.migrate_to_chat_id))

    @router.message(Command("setup"))
    async def setup(
        message: Message, service: FromDishka[ClubService], settings: FromDishka[Settings]
    ):
        if (
            not message.from_user
            or message.from_user.id != settings.owner_id
            or message.sender_chat
        ):
            raise RuleError("Подключить бота может только Ярик со своего аккаунта.")
        if message.chat.type not in {"group", "supergroup"}:
            raise RuleError("Добавь меня в беседу и напиши /setup там.")
        assert message.bot is not None
        bot_user = await message.bot.get_me()
        membership = await message.bot.get_chat_member(message.chat.id, bot_user.id)
        if membership.status != "administrator" and not bot_user.can_read_all_group_messages:
            raise RuleError(
                "Чтобы я видел кружки, дай мне админку без лишних прав или отключи "
                "Group Privacy в BotFather и добавь меня заново. Потом /setup."
            )
        await service.activate(
            message.from_user.id, message.chat.id, datetime.now(settings.tz).date()
        )
        await message.answer(
            "💪 Беседа подключена. Пивной надзор приступил к работе.\n\n" + help_text(settings)
        )

    @router.message(Command("start", "help"))
    async def help_command(
        message: Message, settings: FromDishka[Settings], service: FromDishka[ClubService]
    ):
        if message.chat.type != "private":
            await service.authorize_chat(message.chat.id)
        await message.answer(help_text(settings))

    @router.message(Command("unbind"))
    async def unbind(
        message: Message,
        service: FromDishka[ClubService],
        repository: FromDishka[Repository],
        settings: FromDishka[Settings],
    ):
        if (
            not message.from_user
            or message.from_user.id != settings.owner_id
            or message.sender_chat
        ):
            raise RuleError("Только Ярик может отключить беседу.")
        async with service.mutex.hold():
            await service.authorize_chat(message.chat.id)
            await repository.save_state("chat_id", "")
        await message.answer("Беседа отключена, история сохранена. Для подключения — /setup.")

    @router.message(Command("bind"))
    async def bind(
        message: Message,
        command: CommandObject,
        service: FromDishka[ClubService],
        repository: FromDishka[Repository],
        settings: FromDishka[Settings],
    ):
        if (
            not message.from_user
            or message.from_user.id != settings.owner_id
            or message.sender_chat
        ):
            raise RuleError("Привязки меняет только Ярик.")
        args = (command.args or "").split()
        if len(args) != 2 or not args[1].isdigit() or int(args[1]) <= 0:
            raise RuleError("Формат: /bind @username Telegram_ID")
        async with service.mutex.hold():
            await service.authorize_chat(message.chat.id)
            snapshot = await repository.snapshot()
            member = snapshot.member(args[0])
            for other in snapshot.members:
                if (
                    other.name != member.name
                    and await repository.state(f"member:{other.name}") == args[1]
                ):
                    raise RuleError("Этот Telegram ID уже привязан к другому участнику.")
            await repository.save_state(f"member:{member.name}", args[1])
        await message.answer("Привязка сохранена ✅")

    @router.message(Command("today", "stats", "debts", "sheet"))
    async def summary(
        message: Message,
        command: CommandObject,
        service: FromDishka[ClubService],
        repository: FromDishka[Repository],
        settings: FromDishka[Settings],
    ):
        await service.authorize_chat(message.chat.id)
        if command.command.casefold() == "sheet":
            await message.answer(
                f'🍺 <a href="https://docs.google.com/spreadsheets/d/{settings.spreadsheet_id}/edit">'
                "Пивная бухгалтерия</a>"
            )
            return
        formatter = {"today": report, "stats": statistics, "debts": debts}[
            command.command.casefold()
        ]
        await message.answer(
            formatter(await repository.snapshot(), datetime.now(settings.tz), settings)
        )

    @router.message(Command("done", "miss", "excuse"))
    async def manual(
        message: Message,
        command: CommandObject,
        service: FromDishka[ClubService],
        settings: FromDishka[Settings],
    ):
        if not message.from_user or message.sender_chat:
            raise RuleError("Отправь команду от своего аккаунта, без анонимного админа.")
        args = (command.args or "").split()
        target = args.pop(0) if args and args[0].startswith("@") else None
        now = datetime.now(settings.tz)
        target_day = now.date()
        if args and len(args[0]) == 10 and args[0][4:5] == "-":
            try:
                target_day = datetime.strptime(args.pop(0), "%Y-%m-%d").date()
            except ValueError:
                raise RuleError("Дата должна быть ГГГГ-ММ-ДД.") from None
        status = {"done": Status.DONE, "miss": Status.MISSED, "excuse": Status.EXCUSED}[
            command.command.casefold()
        ]
        changed = await service.record(
            chat_id=message.chat.id,
            actor_id=message.from_user.id,
            username=message.from_user.username,
            target=target,
            day=target_day,
            status=status,
            now=now,
            event_id=f"manual:{message.chat.id}:{message.message_id}",
            reason=" ".join(args),
        )
        if changed:
            await message.answer(
                f"Записал: {status} за {target_day:%d.%m.%Y}. Бухгалтерия всё помнит 🍺"
            )

    @router.message(F.video | F.video_note | (F.document.mime_type.startswith("video/")))
    async def video(
        message: Message,
        service: FromDishka[ClubService],
        repository: FromDishka[Repository],
        settings: FromDishka[Settings],
    ):
        if await repository.state("chat_id") != str(message.chat.id):
            return
        if not message.from_user or message.from_user.is_bot or message.sender_chat:
            return
        if message.forward_origin:
            await message.reply("Пересланный видос не считается. Свой подвиг снимай, хитрец 😏")
            return
        clip = message.video or message.video_note
        if clip is None:
            await message.reply(
                "Пришли как видео или кружок: у файла Telegram не сообщает длительность."
            )
            return
        if not settings.min_video_seconds <= clip.duration <= settings.max_video_seconds:
            await message.reply(
                f"Нужно {settings.min_video_seconds}–{settings.max_video_seconds} секунд, "
                f"а тут {clip.duration}. Минута славы сама себя не снимет 😏"
            )
            return
        sent_at = message.date.astimezone(settings.tz)
        if sent_at >= cutoff_at(sent_at.date(), settings.deadline_time, sent_at):
            await message.reply("Дедлайн уже прошёл. Поздний ролик — Ярику на ручной пересмотр.")
            return
        started = await repository.state("started_on")
        if started and sent_at.date().isoformat() < started:
            return
        evidence = (
            f"chat={message.chat.id}; message={message.message_id}; file={clip.file_unique_id}"
        )
        changed = await service.record(
            chat_id=message.chat.id,
            actor_id=message.from_user.id,
            username=message.from_user.username,
            target=None,
            day=sent_at.date(),
            status=Status.DONE,
            now=datetime.now(settings.tz),
            event_id=f"video:{clip.file_unique_id}",
            evidence=evidence,
            automatic=True,
        )
        if changed:
            try:
                await message.react([ReactionTypeEmoji(emoji="🔥")])
            except TelegramBadRequest:
                logger.info("Reactions are unavailable in the group")
            phrases = [
                "Засчитано. Грудь качается, пиво экономится 💪",
                "Есть контакт с полом! Сегодня пивной налог тебя не тронет 🍺",
                "Заебись, принято! Диван потерял ещё одного бойца 🔥",
            ]
            await message.reply(phrases[message.message_id % len(phrases)])

    return router
