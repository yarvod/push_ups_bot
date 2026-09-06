from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum


class RuleError(Exception):
    """A user-facing violation of club rules."""


class Status(StrEnum):
    DONE = "✅ Отжался"
    EXCUSED = "🟡 Уважительная"
    MISSED = "❌ Проебал"
    PENDING = ""


@dataclass(frozen=True)
class Member:
    name: str
    username: str
    column: int
    initial_misses: int = 0
    telegram_id: int | None = None


@dataclass(frozen=True)
class Day:
    date: date
    row: int
    statuses: dict[str, Status]


@dataclass(frozen=True)
class Snapshot:
    members: list[Member]
    days: list[Day]

    def day(self, target: date) -> Day:
        matches = [day for day in self.days if day.date == target]
        if len(matches) != 1:
            raise RuleError(f"В таблице должна быть ровно одна строка за {target:%d.%m.%Y}.")
        return matches[0]

    def member(self, username: str | None, telegram_id: int | None = None) -> Member:
        for member in self.members:
            if telegram_id is not None and member.telegram_id == telegram_id:
                return member
        for member in self.members:
            if member.username.casefold() == (username or "").lstrip("@").casefold():
                if member.telegram_id is not None and member.telegram_id != telegram_id:
                    raise RuleError("Этот username уже привязан к другому Telegram ID.")
                return member
        raise RuleError("Тебя нет в списке участников на листе «Сводка».")


def cutoff_at(day: date, deadline: time, now: datetime) -> datetime:
    """The configured deadline minute is inclusive (23:59 ends at midnight)."""
    return datetime.combine(day, deadline, tzinfo=now.tzinfo) + timedelta(minutes=1)


def effective_status(day: Day, name: str, now: datetime, deadline: time) -> Status:
    status = day.statuses[name]
    cutoff = cutoff_at(day.date, deadline, now)
    return Status.MISSED if status == Status.PENDING and now >= cutoff else status


def missed_counts(snapshot: Snapshot, now: datetime, deadline: time) -> dict[str, int]:
    return {
        member.name: member.initial_misses
        + sum(
            effective_status(day, member.name, now, deadline) == Status.MISSED
            for day in snapshot.days
            if day.date <= now.date()
        )
        for member in snapshot.members
    }


def beer_debts(snapshot: Snapshot, now: datetime, deadline: time) -> dict[tuple[str, str], int]:
    """Each miss owes one beer to EVERY other member. Never offset opposite debts."""
    counts = missed_counts(snapshot, now, deadline)
    return {
        (debtor.name, creditor.name): counts[debtor.name]
        for debtor in snapshot.members
        for creditor in snapshot.members
        if debtor.name != creditor.name
    }
