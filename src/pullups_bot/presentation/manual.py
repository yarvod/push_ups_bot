from datetime import date, datetime

from aiogram.types import ForceReply, Message

from pullups_bot.application.manual import ManualPrompt, parse_manual
from pullups_bot.application.ports import ManualPromptStore, Repository
from pullups_bot.application.service import ClubService
from pullups_bot.domain.models import RuleError, Status

PROMPT_PREFIX = "📝 Отметка /"


async def handle_manual(
    message: Message,
    command: str,
    arguments: str,
    service: ClubService,
    prompts: ManualPromptStore,
    repository: Repository,
    *,
    default_day: date | None = None,
    prompt_id: int | None = None,
    now: datetime,
) -> None:
    actor = message.from_user
    if not actor or actor.is_bot or message.sender_chat:
        raise RuleError("Отправь команду от своего аккаунта, без анонимного админа.")
    await service.authorize_chat(message.chat.id)
    event_id = (
        f"manual-prompt:{message.chat.id}:{prompt_id}"
        if prompt_id is not None
        else f"manual:{message.chat.id}:{message.message_id}"
    )
    if prompt_id is not None and await repository.event_exists(event_id):
        await message.reply(f"Этот запрос уже выполнен. Для новой отметки отправь /{command}.")
        return
    selected = parse_manual(arguments, default_day or now.date())
    if not arguments.strip() or (command == "excuse" and not selected.reason):
        description = (
            "Напиши причину; для другой даты — дату и причину.\n"
            "Примеры: заболел · 09.09.2026 командировка."
            if command == "excuse"
            else "Напиши «сегодня» или дату, например 09.09.2026. Можно добавить комментарий."
        )
        if arguments.strip():
            description = f"Выбрана дата {selected.day:%d.%m.%Y}.\n" + description
        prefix = " ".join(filter(None, (selected.target, selected.day.isoformat())))
        reply = await message.reply(
            f"{PROMPT_PREFIX}{command}\n{description}\n"
            "Ярик может добавить @username перед датой.\n"
            "Ответь на это сообщение. Для отмены напиши «отмена». Запрос действует сутки.",
            reply_markup=ForceReply(
                selective=True, input_field_placeholder="Дата и причина / сегодня"
            ),
        )
        await prompts.save(
            message.chat.id,
            reply.message_id,
            ManualPrompt(
                actor.id, command, prefix if arguments.strip() else "", now.date().isoformat()
            ),
        )
        return
    status = {"done": Status.DONE, "miss": Status.MISSED, "excuse": Status.EXCUSED}[command]
    await service.record(
        chat_id=message.chat.id,
        actor_id=actor.id,
        username=actor.username,
        target=selected.target,
        day=selected.day,
        status=status,
        now=now,
        event_id=event_id,
        reason=selected.reason,
    )
    # A repeated status can still update the reason; always acknowledge a valid command.
    await message.answer(f"Записал: {status} за {selected.day:%d.%m.%Y}. Бухгалтерия всё помнит 🍺")
