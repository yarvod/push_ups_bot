from datetime import datetime
from html import escape

from pullups_bot.config import Settings
from pullups_bot.domain.models import Snapshot, Status, beer_debts, effective_status, missed_counts


def report(snapshot: Snapshot, now: datetime, settings: Settings) -> str:
    day = snapshot.day(now.date())
    lines = [f"💪 Отжимательный перекличник · {now:%d.%m.%Y}"]
    for member in snapshot.members:
        status = effective_status(day, member.name, now, settings.deadline_time)
        lines.append(f"{escape(member.name)} — {status or '⏳ пока без видоса'}")
    lines.append(f"\nДедлайн {settings.deadline_time:%H:%M}, {escape(settings.timezone)}.")
    lines.append(
        "Кто уже сделал — красавчик. Остальные, диван сам себя не продавит, но подождёт 😏"
    )
    return "\n".join(lines)


def statistics(snapshot: Snapshot, now: datetime, settings: Settings) -> str:
    misses = missed_counts(snapshot, now, settings.deadline_time)
    lines = ["📊 Табель силы воли и пивных инвестиций"]
    for member in snapshot.members:
        done = sum(
            d.statuses[member.name] == Status.DONE for d in snapshot.days if d.date <= now.date()
        )
        excused = sum(
            d.statuses[member.name] == Status.EXCUSED for d in snapshot.days if d.date <= now.date()
        )
        lines.append(
            f"{escape(member.name)}: ✅ {done} · 🟡 {excused} · ❌ {misses[member.name]} "
            f"(включая стартовые {member.initial_misses}) · "
            f"должен 🍺 {misses[member.name] * (len(snapshot.members) - 1)}"
        )
    return "\n".join(lines)


def debts(snapshot: Snapshot, now: datetime, settings: Settings) -> str:
    ledger = beer_debts(snapshot, now, settings.deadline_time)
    lines = ["🍺 Кто кому должен — бухгалтерия без фокусов"]
    for (debtor, creditor), count in ledger.items():
        if count:
            lines.append(f"{escape(debtor)} → {escape(creditor)}: {count} 🍺")
    if len(lines) == 1:
        lines.append("Долгов нет. Подозрительно спортивная компания.")
    lines.append("\nВстречные долги НЕ вычитаем. Оба проебались — оба проставляются.")
    return "\n".join(lines)


def help_text(settings: Settings) -> str:
    return (
        "💪 Пивной надзор на связи. Кружок или видео с отжиманиями — сюда.\n"
        f"Длительность: {settings.min_video_seconds}–{settings.max_video_seconds} секунд. "
        "Пересылки и повтор одного видео не принимаю.\n\n"
        "/today — кто сегодня отжался\n/stats — общая статистика\n/debts — кто кому должен пива\n"
        "/done — вручную отметить себя\n/miss — честно признать проёб\n"
        "/excuse причина — отметить себе уважительную за сегодня до дедлайна\n"
        "/sheet — открыть таблицу\n\n"
        "Можно также ответить мне или тегнуть меня и написать по-человечески: "
        "«покажи долги», «я отжался», «поставь уважительную, я заболел». "
        "Права и сроки те же, что у команд.\n\n"
        "Команды /done, /miss и /excuse без текста попросят детали ответным сообщением. "
        "Можно написать сразу: /done сегодня или /excuse заболел. "
        "Даты: ДД.ММ.ГГГГ или ГГГГ-ММ-ДД. За прошлые дни исправляет только Ярик.\n\n"
        "Для Ярика: /setup, /unbind; /done и /miss @username [ГГГГ-ММ-ДД] [причина]; "
        "/excuse @username [ГГГГ-ММ-ДД] причина; "
        "/bind @username Telegram_ID — исправить привязку участника.\n\n"
        f"Напоминание {settings.reminder_time:%H:%M}, сводка {settings.summary_time:%H:%M}, "
        f"дедлайн {settings.deadline_time:%H:%M} включительно. {escape(settings.timezone)}.\n"
        "Сам ролик не смотрю: технику и честность контролирует ваша банда. "
        "Не сделал — по пиву каждому. Без взаимозачётов, хитрожопые 😏"
    )
