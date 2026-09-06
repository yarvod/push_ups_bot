from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from pullups_bot.domain.models import Day, Snapshot, Status, beer_debts, effective_status

TZ = ZoneInfo("Europe/Moscow")


@pytest.mark.parametrize(
    "stamp,expected",
    [
        ("2026-09-06T13:00:00", Status.PENDING),
        ("2026-09-06T18:00:00", Status.PENDING),
        ("2026-09-06T23:59:59", Status.PENDING),
        ("2026-09-07T00:00:00", Status.MISSED),
    ],
)
def test_inclusive_deadline_minute(stamp, expected):
    day = Day(date(2026, 9, 6), 18, {"a": Status.PENDING})
    assert (
        effective_status(day, "a", datetime.fromisoformat(stamp).replace(tzinfo=TZ), time(23, 59))
        == expected
    )


def test_everyone_owes_everyone_even_when_both_miss(repo):
    day = repo.days[1]
    day.statuses.update(
        {"Саня": Status.MISSED, "Ярик": Status.MISSED, "Тёма": Status.DONE, "Лёша": Status.EXCUSED}
    )
    snapshot = Snapshot(repo.members, [day])
    debts = beer_debts(snapshot, datetime(2026, 9, 6, 18, tzinfo=TZ), time(23, 59))
    assert debts["Саня", "Ярик"] == 1
    assert debts["Ярик", "Саня"] == 4  # three initial misses remain
    assert debts["Тёма", "Саня"] == 2
    assert debts["Лёша", "Саня"] == 0
    assert len(debts) == 12


def test_future_rows_never_charge_beer(repo):
    future = repo.days[2]
    future.statuses["Саня"] = Status.MISSED
    ledger = beer_debts(
        Snapshot(repo.members, [future]), datetime(2026, 9, 6, tzinfo=TZ), time(23, 59)
    )
    assert ledger["Саня", "Ярик"] == 0


def test_custom_deadline():
    day = Day(date(2026, 9, 6), 18, {"a": Status.PENDING})
    assert (
        effective_status(day, "a", datetime(2026, 9, 6, 18, 1, tzinfo=TZ), time(18))
        == Status.MISSED
    )
