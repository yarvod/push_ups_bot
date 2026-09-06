from unittest.mock import AsyncMock

from pullups_bot.domain.models import Status
from pullups_bot.infrastructure.sheets import DAILY, JOURNAL, SheetsRepository


async def test_mark_and_audit_are_one_atomic_batch(repo):
    sheets = object.__new__(SheetsRepository)
    sheets.tabs = {DAILY: {"sheetId": 1}, JOURNAL: {"sheetId": 2}}
    sheets._events = set()
    sheets.event_exists = AsyncMock(return_value=False)
    sheets.batch = AsyncMock()
    await sheets.mark(
        repo.days[1], repo.members[0], Status.DONE, "event", 101, "reason", "evidence"
    )
    requests = sheets.batch.call_args.args[0]
    assert len(requests) == 2
    assert requests[0]["updateCells"]["start"] == {"sheetId": 1, "rowIndex": 17, "columnIndex": 2}
    assert requests[0]["updateCells"]["fields"] == "userEnteredValue,note"
    assert requests[1]["appendCells"]["sheetId"] == 2
    assert (
        requests[1]["appendCells"]["rows"][0]["values"][0]["userEnteredValue"]["stringValue"]
        == "event"
    )


async def test_negative_event_cache_never_hides_uncertain_google_commit():
    sheets = object.__new__(SheetsRepository)
    sheets._events = set()
    sheets.values = AsyncMock(side_effect=[[[]], [[["committed-event"]]]])
    assert not await sheets.event_exists("committed-event")
    assert await sheets.event_exists("committed-event")
    assert sheets.values.await_count == 2
