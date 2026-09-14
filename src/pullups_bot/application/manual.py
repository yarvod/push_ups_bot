import re
from dataclasses import dataclass
from datetime import date, datetime

from pullups_bot.domain.models import RuleError


@dataclass(frozen=True)
class ManualInput:
    target: str | None
    day: date
    reason: str


@dataclass(frozen=True)
class ManualPrompt:
    actor_id: int
    command: str
    prefix: str
    day: str


def parse_manual(text: str, default_day: date) -> ManualInput:
    args = text.split()
    target = args.pop(0) if args and args[0].startswith("@") else None
    day = default_day
    if args and args[0].casefold() == "сегодня":
        args.pop(0)
    elif args and re.match(r"^\d+[.\-/]", args[0]):
        raw = args.pop(0)
        for pattern, fmt in (
            (r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"),
            (r"\d{1,2}\.\d{1,2}\.\d{4}", "%d.%m.%Y"),
        ):
            if re.fullmatch(pattern, raw):
                try:
                    day = datetime.strptime(raw, fmt).date()
                    break
                except ValueError:
                    pass
        else:
            raise RuleError("Не понял дату. Пример: 09.09.2026 или 2026-09-09.")
    return ManualInput(target, day, " ".join(args))


def complete_manual(prompt: ManualPrompt, answer: str) -> str:
    first = answer.split()[0] if answer.split() else ""
    if first.startswith("@"):
        return answer
    if first.casefold() == "сегодня" or re.match(r"^\d+[.\-/]", first):
        target = parse_manual(prompt.prefix, date.fromisoformat(prompt.day)).target
        return " ".join(filter(None, (target, answer)))
    return " ".join(filter(None, (prompt.prefix, answer)))
