from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.methods import GetChatMember, GetMe, SendMessage
from aiogram.types import ChatMemberAdministrator, Message, Update, User
from dishka import Provider, Scope, make_async_container

from pullups_bot.application.ports import Mutex, Repository
from pullups_bot.application.service import ClubService
from pullups_bot.bootstrap import create_dispatcher
from pullups_bot.config import Settings
from pullups_bot.domain.models import Status
from pullups_bot.presentation import handlers

TZ = ZoneInfo("Europe/Moscow")


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 9, 6, 21, tzinfo=TZ).astimezone(tz)


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109
        self.calls.append(method)
        bot_user = User(id=123456, is_bot=True, first_name="bot", username="pushups_test_bot")
        if isinstance(method, GetMe):
            return bot_user
        if isinstance(method, GetChatMember):
            return ChatMemberAdministrator(
                user=bot_user,
                can_be_edited=False,
                is_anonymous=False,
                can_manage_chat=True,
                can_delete_messages=False,
                can_manage_video_chats=False,
                can_restrict_members=False,
                can_promote_members=False,
                can_change_info=False,
                can_invite_users=False,
                can_post_stories=False,
                can_edit_stories=False,
                can_delete_stories=False,
                can_send_welcome_messages=False,
            )
        if isinstance(method, SendMessage):
            return Message(
                message_id=500,
                date=FrozenDateTime.now(TZ),
                chat={"id": method.chat_id, "type": "supergroup"},
                text=method.text,
            )
        return True


@pytest.fixture
async def telegram(repo, service, settings, monkeypatch):
    monkeypatch.setattr(handlers, "datetime", FrozenDateTime)
    session = RecordingSession()
    bot = Bot(
        settings.token.get_secret_value(),
        session=session,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    provider = Provider(scope=Scope.APP)
    for tp in (Settings, Repository, Mutex, ClubService, Bot):
        provider.from_context(tp)
    container = make_async_container(
        provider,
        context={
            Settings: settings,
            Repository: repo,
            Mutex: service.mutex,
            ClubService: service,
            Bot: bot,
        },
    )
    dispatcher = create_dispatcher(container)
    yield dispatcher, bot, session, container
    await container.close()
    await bot.session.close()


def message_update(**fields):
    return Update.model_validate(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "date": int(FrozenDateTime.now(TZ).timestamp()),
                "chat": {"id": -10042, "type": "supergroup"},
                "from": {"id": 101, "is_bot": False, "first_name": "Саня", "username": "weebat"},
                **fields,
            },
        }
    )


def clip(duration=60, unique="unique1"):
    return {"file_id": "file1", "file_unique_id": unique, "length": 240, "duration": duration}


async def test_real_aiogram_dishka_video_pipeline(telegram, repo):
    dp, bot, session, _ = telegram
    await dp.feed_update(bot, message_update(video_note=clip()))
    assert repo.days[1].statuses["Саня"] == Status.DONE
    assert any(call.__api_method__ == "setMessageReaction" for call in session.calls)
    assert any(call.__api_method__ == "sendMessage" for call in session.calls)
    await dp.feed_update(bot, message_update(video_note=clip()))
    assert repo.writes == 1


@pytest.mark.parametrize("duration", [1, 49, 91, 600])
async def test_wrong_duration_rejected(telegram, repo, duration):
    dp, bot, _, _ = telegram
    await dp.feed_update(bot, message_update(video_note=clip(duration)))
    assert repo.writes == 0


async def test_forwarded_video_rejected(telegram, repo):
    dp, bot, _, _ = telegram
    await dp.feed_update(
        bot,
        message_update(
            video_note=clip(),
            forward_origin={
                "type": "hidden_user",
                "date": int(FrozenDateTime.now(TZ).timestamp()),
                "sender_user_name": "someone",
            },
        ),
    )
    assert repo.writes == 0


async def test_unknown_chat_ignored(telegram, repo):
    dp, bot, session, _ = telegram
    await dp.feed_update(bot, message_update(video_note=clip(), chat={"id": -999, "type": "group"}))
    assert not session.calls and repo.writes == 0


async def test_only_owner_invitation_accepted(telegram):
    dp, bot, session, _ = telegram
    update = Update.model_validate(
        {
            "update_id": 20,
            "my_chat_member": {
                "chat": {"id": -999, "type": "group"},
                "date": int(FrozenDateTime.now(TZ).timestamp()),
                "from": {"id": 101, "first_name": "other", "is_bot": False},
                "old_chat_member": {
                    "status": "left",
                    "user": {"id": bot.id, "first_name": "bot", "is_bot": True},
                },
                "new_chat_member": {
                    "status": "member",
                    "user": {"id": bot.id, "first_name": "bot", "is_bot": True},
                },
            },
        }
    )
    await dp.feed_update(bot, update)
    assert session.calls[0].__api_method__ == "leaveChat"


async def test_owner_setup(telegram):
    dp, bot, session, _ = telegram
    await dp.feed_update(
        bot,
        message_update(
            text="/setup",
            entities=[{"type": "bot_command", "offset": 0, "length": 6}],
            **{
                "from": {
                    "id": 718724903,
                    "first_name": "Ярик",
                    "is_bot": False,
                    "username": "yarvod",
                }
            },
        ),
    )
    assert any(call.__api_method__ == "sendMessage" for call in session.calls)


async def test_manual_command_injection(telegram, repo):
    dp, bot, _, _ = telegram
    await dp.feed_update(
        bot,
        message_update(text="/done", entities=[{"type": "bot_command", "offset": 0, "length": 5}]),
    )
    assert repo.days[1].statuses["Саня"] == Status.DONE


async def test_scheduler_sends_once_per_day(telegram, repo, settings, monkeypatch):
    from pullups_bot import worker

    monkeypatch.setattr(worker, "datetime", FrozenDateTime)
    dp, bot, session, container = telegram
    ctx = {"container": container, "settings": settings, "dispatcher": dp, "job_try": 1}
    await worker.scheduled_tick(ctx)
    await worker.scheduled_tick(ctx)
    assert len([call for call in session.calls if isinstance(call, SendMessage)]) == 1
    assert repo.states["last_summary"] == "2026-09-06"


async def test_scheduler_tags_only_pending(telegram, repo, settings, monkeypatch):
    from pullups_bot import worker

    class NoonDateTime(FrozenDateTime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 6, 13, 0, 5, tzinfo=TZ).astimezone(tz)

    monkeypatch.setattr(worker, "datetime", NoonDateTime)
    repo.days[1].statuses["Тёма"] = Status.DONE
    repo.days[1].statuses["Ярик"] = Status.EXCUSED
    dp, bot, session, container = telegram
    await worker.scheduled_tick({"container": container, "settings": settings})
    messages = [call.text for call in session.calls if isinstance(call, SendMessage)]
    assert len(messages) == 1
    assert "@weebat" in messages[0] and "@pukenzo" in messages[0]
    assert "@atep_art" not in messages[0] and "@yarvod" not in messages[0]
