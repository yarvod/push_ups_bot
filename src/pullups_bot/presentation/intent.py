"""Conservative Russian-language routing for messages addressed to the bot."""

import re
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Intent:
    command: str
    args: str = ""


_TARGET = re.compile(r"(?<!\w)@[A-Za-z][A-Za-z0-9_]{4,31}\b")
_DATE = re.compile(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4})\b")
_ID = re.compile(r"\b\d{1,12}\b")


def _has(text: str, *stems: str) -> bool:
    return any(stem in text for stem in stems)


def _day(text: str, today: date) -> str:
    found = _DATE.search(text)
    if found:
        return found.group()
    if "позавчера" in text:
        return (today - timedelta(days=2)).isoformat()
    if "вчера" in text:
        return (today - timedelta(days=1)).isoformat()
    if "завтра" in text:
        return (today + timedelta(days=1)).isoformat()
    return "сегодня"


def _reason(text: str, command: str) -> str:
    for marker in ("потому что", "так как", "из-за", "причина:", "причина "):
        if marker in text:
            return text.split(marker, 1)[1].strip(" ,.:;—-")
    if "," in text:
        return text.split(",", 1)[1].strip(" ,.:;—-")
    if command == "excuse":
        match = re.search(r"\b(?:я\s+)?(?:заболел[а]?|болею|болел[а]?)\b", text)
        if match:
            return match.group()
        match = re.search(r"\bпо\s+болезни\b", text)
        if match:
            return match.group()
    return ""


def parse_intent(text: str, today: date) -> Intent | None:
    """Return one explicit command, or None for ordinary conversation and questions."""
    normalized = " ".join(text.casefold().split()).strip(" ,.:;!?—-")
    if not normalized or len(normalized) > 500:
        return None
    if re.search(
        r"\b(?:не\s+(?:надо|ставь|отмечай|записывай|отключай|подключай)|отмени)\b", normalized
    ):
        return None

    target_match = _TARGET.search(text)
    target = target_match.group() if target_match else ""
    day = _day(normalized, today)
    question = normalized.startswith(("как ", "можно ли ", "что будет", "почему ", "сколько "))

    if not question and _has(normalized, "привяж", "привязать", "свяжи"):
        user_id = _ID.search(text)
        return Intent("bind", f"{target} {user_id.group() if user_id else ''}".strip())
    if not question and _has(normalized, "отключи бесед", "отвяжи бесед", "убери бота из бесед"):
        return Intent("unbind")
    if not question and _has(normalized, "подключи бесед", "включи бесед", "настрой бота в бесед"):
        return Intent("setup")

    if _has(normalized, "долг", "кому должен", "сколько пива", "пивной счёт", "пивной счет"):
        return Intent("debts")
    if _has(
        normalized,
        "статистик",
        "сколько отжал",
        "сколько отжался",
        "сколько я отжал",
        "сколько я отжался",
        "мой прогресс",
        "мои результат",
    ):
        return Intent("stats")
    if _has(normalized, "таблиц", "ссылк на лист", "гугл лист"):
        return Intent("sheet")
    if _has(
        normalized,
        "отметки сегодня",
        "отметки за сегодня",
        "что сегодня",
        "сводк",
        "отчёт",
        "отчет",
    ):
        return Intent("today")
    if _has(
        normalized, "помощ", "список команд", "покажи команды", "что ты умеешь", "как пользоваться"
    ):
        return Intent("help")

    action = _has(normalized, "отмет", "постав", "запиш", "засчит", "простав")
    own_report = _has(
        normalized,
        "я отжал",
        "я отжался",
        "я сделал отжим",
        "я не отжался",
        "я не сделал отжим",
        "я пропуст",
        "я заболел",
        "я болею",
    )
    if not question and (action or own_report):
        command = None
        if _has(normalized, "уважитель", "боле", "заболел", "по болезни"):
            command = "excuse"
        elif _has(
            normalized, "пропуск", "прогул", "пропуст", "не отжал", "не отжался", "не сделал отжим"
        ):
            command = "miss"
        elif _has(normalized, "отжал", "отжался", "отжим", "сделал", "засчит", "выполн"):
            command = "done"
        if command:
            reason = _reason(normalized, command)
            return Intent(command, " ".join(filter(None, (target, day, reason))))

    return None
