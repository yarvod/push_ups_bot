from datetime import date, datetime, time, timedelta

from pullups_bot.application.ports import Mutex, Repository
from pullups_bot.domain.models import Member, RuleError, Status, cutoff_at


class ClubService:
    """Application rules; neither Telegram nor Google API types belong here."""

    def __init__(self, repository: Repository, mutex: Mutex, owner_id: int, deadline: time):
        self.repository = repository
        self.mutex = mutex
        self.owner_id = owner_id
        self.deadline = deadline

    async def remember_invitation(self, actor_id: int, chat_id: int) -> None:
        if actor_id != self.owner_id:
            raise RuleError("Подключить бота может только Ярик.")
        async with self.mutex.hold():
            await self.repository.save_state(f"admitted:{chat_id}", str(self.owner_id))

    async def migrate_chat(self, old_chat: int, new_chat: int, occurred_at: int) -> None:
        async with self.mutex.hold():
            active = await self.repository.state("chat_id")
            admitted = await self.repository.state(f"admitted:{old_chat}")
            if admitted != str(self.owner_id) and active != str(old_chat):
                return
            # Both migration messages may arrive. Do not recreate a consumed admission grant.
            migration_key = f"migrated:{old_chat}"
            if await self.repository.state(migration_key) != str(new_chat):
                await self.repository.save_state(f"admitted:{new_chat}", str(self.owner_id))
                await self.repository.save_state(f"migration_grant:{new_chat}", str(occurred_at))
                await self.repository.save_state(migration_key, str(new_chat))
            if active == str(old_chat):
                await self.repository.save_state("chat_id", str(new_chat))

    async def accept_migration(self, chat_id: int, occurred_at: int, event_id: int) -> bool:
        async with self.mutex.hold():
            receipt_key = f"migration_accepted:{chat_id}"
            receipt = await self.repository.state(receipt_key)
            if receipt:
                return receipt == str(event_id)
            grant_key = f"migration_grant:{chat_id}"
            grant = await self.repository.state(grant_key)
            admitted = await self.repository.state(f"admitted:{chat_id}")
            if not grant or admitted != str(self.owner_id):
                return False
            if abs(int(grant) - occurred_at) > 10:
                return False
            # A worker retry must accept the same update, but never another invitation.
            await self.repository.save_state(receipt_key, str(event_id))
            await self.repository.save_state(grant_key, "")
            return True

    async def forget_chat(self, chat_id: int) -> None:
        async with self.mutex.hold():
            await self.repository.save_state(f"admitted:{chat_id}", "")
            await self.repository.save_state(f"migration_grant:{chat_id}", "")
            await self.repository.save_state(f"migration_accepted:{chat_id}", "")
            if await self.repository.state("chat_id") == str(chat_id):
                await self.repository.save_state("chat_id", "")

    async def activate(self, actor_id: int, chat_id: int, today: date) -> None:
        if actor_id != self.owner_id:
            raise RuleError("Подключать беседу может только Ярик. Не лезь к рубильнику 😏")
        async with self.mutex.hold():
            existing = await self.repository.state("chat_id")
            if existing and int(existing) != chat_id:
                raise RuleError("Уже подключён другой чат. Сначала /unbind в старой беседе.")
            await self.repository.save_state("chat_id", str(chat_id))
            if not await self.repository.state("started_on"):
                await self.repository.save_state("started_on", today.isoformat())

    async def authorize_chat(self, chat_id: int) -> None:
        if await self.repository.state("chat_id") != str(chat_id):
            raise RuleError("Беседа не подключена. Ярик, используй /setup.")

    async def identify(self, actor_id: int, username: str | None) -> Member:
        snapshot = await self.repository.snapshot()
        # Persisted numeric IDs survive Telegram username changes.
        for member in snapshot.members:
            bound = await self.repository.state(f"member:{member.name}")
            if bound and int(bound) == actor_id:
                return member
        member = snapshot.member(username)
        bound = await self.repository.state(f"member:{member.name}")
        if bound and int(bound) != actor_id:
            raise RuleError("Этот участник уже привязан к другому Telegram ID. Нужен Ярик.")
        await self.repository.save_state(f"member:{member.name}", str(actor_id))
        return member

    async def record(
        self,
        *,
        chat_id: int,
        actor_id: int,
        username: str | None,
        target: str | None,
        day: date,
        status: Status,
        now: datetime,
        event_id: str,
        reason: str = "",
        evidence: str = "",
        automatic: bool = False,
    ) -> bool:
        async with self.mutex.hold():
            await self.authorize_chat(chat_id)
            if await self.repository.event_exists(event_id):
                return False
            snapshot = await self.repository.snapshot()
            if actor_id == self.owner_id and target:
                matches = [
                    m
                    for m in snapshot.members
                    if m.username.casefold() == target.lstrip("@").casefold()
                    or m.name.casefold() == target.casefold()
                ]
                if len(matches) != 1:
                    raise RuleError("Не нашёл участника. Используй @username из таблицы.")
                member = matches[0]
            else:
                member = await self.identify(actor_id, username)
                if target and target.lstrip("@").casefold() != member.username.casefold():
                    raise RuleError("За другого участника отметки ставит только Ярик.")
            if day > now.date():
                raise RuleError("Отжимания из будущего не принимаю, пророк хренов 😏")
            if not automatic and actor_id != self.owner_id:
                if day != now.date() or now >= cutoff_at(day, self.deadline, now):
                    raise RuleError("День закрыт. Исправить прошлое может только Ярик.")
                if status == Status.EXCUSED:
                    raise RuleError(
                        "Уважительную причину подтверждает Ярик: /excuse @username причина."
                    )
            if status == Status.EXCUSED and not reason.strip():
                raise RuleError("Укажи уважительную причину после команды.")
            selected = snapshot.day(day)
            if automatic and selected.statuses[member.name] in (Status.DONE, Status.EXCUSED):
                return False
            return await self.repository.mark(
                selected,
                member,
                status,
                event_id,
                actor_id,
                reason[:500],
                evidence,
            )

    async def finalize(self, now: datetime) -> None:
        """Catch up after restarts, only for days since this chat was activated."""
        async with self.mutex.hold():
            started = await self.repository.state("started_on")
            if not started or not await self.repository.state("chat_id"):
                return
            await self.repository.ensure_dates(now.date() + timedelta(days=30))
            snapshot = await self.repository.snapshot()
            for day in snapshot.days:
                if date.fromisoformat(started) <= day.date <= now.date():
                    if now >= cutoff_at(day.date, self.deadline, now):
                        key = f"close:{day.date.isoformat()}"
                        if not await self.repository.event_exists(key):
                            await self.repository.close_day(day, key)
