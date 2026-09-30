import logging
from datetime import date, datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import ChatMemberUpdated, Message, ReactionTypeEmoji, Update
from dishka.integrations.aiogram import FromDishka

from pullups_bot.application.manual import complete_manual
from pullups_bot.application.ports import BanterQueue, ManualPromptStore, Repository
from pullups_bot.application.service import ClubService
from pullups_bot.config import Settings
from pullups_bot.domain.models import AdmissionPending, RuleError, Status, cutoff_at
from pullups_bot.presentation.intent import parse_intent
from pullups_bot.presentation.manual import PROMPT_PREFIX, handle_manual
from pullups_bot.presentation.texts import debts, help_text, report, statistics

logger = logging.getLogger(__name__)


def create_router() -> Router:
    router = Router(name="club")

    @router.my_chat_member()
    async def membership(
        event: ChatMemberUpdated,
        event_update: Update,
        service: FromDishka[ClubService],
        settings: FromDishka[Settings],
        bot: FromDishka[Bot],
        defer_unknown_invitation: bool = True,
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
                if await service.accept_migration(
                    event.chat.id, int(event.date.timestamp()), event_update.update_id
                ):
                    return
                if defer_unknown_invitation:
                    raise AdmissionPending
                await bot.leave_chat(event.chat.id)
                return
            await service.remember_invitation(event.from_user.id, event.chat.id)
            # /setup explicitly binds the chat after Telegram privacy is configured.
        if not is_present:
            await service.forget_chat(event.chat.id)

    @router.message(F.migrate_to_chat_id | F.migrate_from_chat_id)
    async def migration(message: Message, service: FromDishka[ClubService]):
        old_chat = message.migrate_from_chat_id or message.chat.id
        new_chat = message.migrate_to_chat_id or message.chat.id
        await service.migrate_chat(old_chat, new_chat, int(message.date.timestamp()))

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
        # Owner confirmation also covers bots added before invitation tracking was introduced.
        await service.remember_invitation(message.from_user.id, message.chat.id)
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
        prompts: FromDishka[ManualPromptStore],
        repository: FromDishka[Repository],
    ):
        await handle_manual(
            message,
            command.command.casefold(),
            command.args or "",
            service,
            prompts,
            repository,
            now=datetime.now(settings.tz),
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
                f"а тут {clip.duration}. Подгони хронометраж, Спилберг 😏"
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
                await message.react([ReactionTypeEmoji(emoji="🍾")])
            except TelegramBadRequest:
                logger.info("Reactions are unavailable in the group")
            phrases = [
                "🍻 Засчитано. Грудь качается, пиво экономится 💪",
                "🍻 Есть контакт с полом! Сегодня пивной налог тебя не тронет.",
                "🍻 Заебись, принято! Диван потерял ещё одного бойца 💪",
            ]
            await message.reply(phrases[message.message_id % len(phrases)])

    @router.message(F.text)
    async def addressed_text(
        message: Message,
        bot: FromDishka[Bot],
        repository: FromDishka[Repository],
        prompts: FromDishka[ManualPromptStore],
        service: FromDishka[ClubService],
        settings: FromDishka[Settings],
        banter: FromDishka[BanterQueue],
    ):
        if not message.from_user or message.from_user.is_bot or message.sender_chat:
            return
        replied = message.reply_to_message
        reply_to_bot = bool(replied and replied.from_user and replied.from_user.id == bot.id)
        text = message.text or ""
        mentions = [
            entity.extract_from(text)
            for entity in message.entities or []
            if entity.type == "mention"
        ]
        bot_mention = ""
        if mentions:
            bot_user = await bot.get_me()
            bot_mention = next(
                (
                    name
                    for name in mentions
                    if name.casefold() == f"@{bot_user.username}".casefold()
                ),
                "",
            )
        if not reply_to_bot and not bot_mention:
            return
        if any(entity.type == "bot_command" for entity in message.entities or []):
            return
        active_chat = await repository.state("chat_id") == str(message.chat.id)
        if not active_chat:
            intent = parse_intent(text.replace(bot_mention, ""), datetime.now(settings.tz).date())
            if intent and intent.command == "setup":
                await setup(message, service, settings)
            return
        if reply_to_bot and replied:
            prompt = await prompts.get(message.chat.id, replied.message_id)
            if prompt:
                if message.from_user.id != prompt.actor_id:
                    await message.reply(
                        "Это чужой запрос. Для своей отметки выбери команду в меню."
                    )
                    return
                if text.strip().casefold() == "отмена":
                    if await repository.event_exists(
                        f"manual-prompt:{message.chat.id}:{replied.message_id}"
                    ):
                        await message.reply("Отметка уже записана. Измени её новой командой.")
                        return
                    await prompts.delete(message.chat.id, replied.message_id)
                    await message.reply("Отменил. Ничего не записано.")
                    return
                arguments = complete_manual(prompt, text)
                await handle_manual(
                    message,
                    prompt.command,
                    arguments,
                    service,
                    prompts,
                    repository,
                    default_day=date.fromisoformat(prompt.day),
                    prompt_id=replied.message_id,
                    now=datetime.now(settings.tz),
                )
                return
            if (replied.text or "").startswith(PROMPT_PREFIX):
                await message.reply("Запрос истёк или отменён. Выбери команду заново.")
                return

        intent = parse_intent(text.replace(bot_mention, ""), datetime.now(settings.tz).date())
        if intent:
            command = CommandObject(command=intent.command, args=intent.args)
            if intent.command == "setup":
                await setup(message, service, settings)
            elif intent.command == "unbind":
                await unbind(message, service, repository, settings)
            elif intent.command == "bind":
                await bind(message, command, service, repository, settings)
            elif intent.command == "help":
                await help_command(message, settings, service)
            elif intent.command in {"today", "stats", "debts", "sheet"}:
                await summary(message, command, service, repository, settings)
            else:
                await manual(message, command, service, settings, prompts, repository)
            return

        await banter.enqueue(
            message.chat.id, message.message_id, text, (replied.text or "") if replied else ""
        )

    return router
