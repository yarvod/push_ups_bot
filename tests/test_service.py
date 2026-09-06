from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from pullups_bot.domain.models import RuleError, Status

NOW = datetime(2026, 9, 6, 21, tzinfo=ZoneInfo("Europe/Moscow"))


def record_args(**overrides):
    return (
        dict(
            chat_id=-10042,
            actor_id=101,
            username="weebat",
            target=None,
            day=date(2026, 9, 6),
            status=Status.DONE,
            now=NOW,
            event_id="event1",
        )
        | overrides
    )


async def test_only_owner_can_activate(service):
    with pytest.raises(RuleError):
        await service.activate(101, -123, NOW.date())


async def test_no_second_chat(service):
    with pytest.raises(RuleError):
        await service.activate(718724903, -123, NOW.date())


async def test_wrong_chat_rejected_before_mutation(service, repo):
    with pytest.raises(RuleError):
        await service.record(**record_args(chat_id=-555))
    assert repo.writes == 0


async def test_repeated_event_is_idempotent(service, repo):
    assert await service.record(**record_args())
    assert not await service.record(**record_args())
    assert repo.writes == 1


async def test_only_owner_marks_others(service, repo):
    with pytest.raises(RuleError):
        await service.record(**record_args(target="@atep_art"))
    assert repo.writes == 0


async def test_owner_can_correct_previous_day(service, repo):
    await service.record(
        **record_args(
            actor_id=718724903, username="yarvod", target="@atep_art", day=date(2026, 9, 5)
        )
    )
    assert repo.days[0].statuses["Тёма"] == Status.DONE


async def test_nonowner_cannot_correct_previous_day(service):
    with pytest.raises(RuleError):
        await service.record(**record_args(day=date(2026, 9, 5)))


@pytest.mark.parametrize("actor,target,reason", [(101, None, "болею"), (718724903, "@weebat", "")])
async def test_excuse_needs_owner_and_reason(service, actor, target, reason):
    with pytest.raises(RuleError):
        await service.record(
            **record_args(actor_id=actor, target=target, status=Status.EXCUSED, reason=reason)
        )


async def test_excuse_is_saved(service, repo):
    await service.record(
        **record_args(actor_id=718724903, target="@weebat", status=Status.EXCUSED, reason="болеет")
    )
    assert repo.days[1].statuses["Саня"] == Status.EXCUSED


async def test_id_survives_username_change(service):
    await service.record(**record_args())
    await service.record(**record_args(username="renamed", event_id="event2", status=Status.MISSED))


async def test_username_cannot_be_taken_by_another_id(service):
    await service.record(**record_args())
    with pytest.raises(RuleError):
        await service.record(**record_args(actor_id=102, event_id="event2"))


async def test_second_video_does_not_replace_excuse(service, repo):
    repo.days[1].statuses["Саня"] = Status.EXCUSED
    assert not await service.record(**record_args(automatic=True))
    assert repo.writes == 0


async def test_finalization_catches_up_and_preserves_history(service, repo):
    repo.days[1].statuses["Тёма"] = Status.DONE
    repo.days[1].statuses["Ярик"] = Status.EXCUSED
    await service.finalize(datetime(2026, 9, 7, 0, 1, tzinfo=NOW.tzinfo))
    assert repo.days[0].statuses["Саня"] == Status.PENDING  # before activation
    assert repo.days[1].statuses["Саня"] == Status.MISSED
    assert repo.days[1].statuses["Тёма"] == Status.DONE
    assert repo.days[1].statuses["Ярик"] == Status.EXCUSED
    assert repo.days[2].statuses["Саня"] == Status.PENDING
    count = len(repo.events)
    await service.finalize(datetime(2026, 9, 7, 0, 2, tzinfo=NOW.tzinfo))
    assert len(repo.events) == count


async def test_valid_queued_video_corrects_automatic_miss(service, repo):
    repo.days[1].statuses["Саня"] = Status.MISSED
    await service.record(
        **record_args(automatic=True, now=datetime(2026, 9, 7, 0, 1, tzinfo=NOW.tzinfo))
    )
    assert repo.days[1].statuses["Саня"] == Status.DONE
