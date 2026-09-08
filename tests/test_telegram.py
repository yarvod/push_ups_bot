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
from pullups_bot.domain.models import AdmissionPending, RuleError, Status
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
    reaction = next(call for call in session.calls if call.__api_method__ == "setMessageReaction")
    assert reaction.reaction[0].emoji == "🍾"
    assert any(call.__api_method__ == "sendMessage" for call in session.calls)
    await dp.feed_update(bot, message_update(video_note=clip()))
    assert repo.writes == 1


@pytest.mark.parametrize("duration", [1, 49, 601, 1200])
async def test_wrong_duration_rejected(telegram, repo, duration):
    dp, bot, _, _ = telegram
    await dp.feed_update(bot, message_update(video_note=clip(duration)))
    assert repo.writes == 0


@pytest.mark.parametrize("duration", [50, 91, 103, 181, 240, 600])
async def test_long_video_accepted(telegram, repo, duration):
    dp, bot, _, _ = telegram
    await dp.feed_update(
        bot,
        message_update(
            video={
                "file_id": "video1",
                "file_unique_id": "long-video",
                "width": 1280,
                "height": 720,
                "duration": duration,
            }
        ),
    )
    assert repo.days[1].statuses["Саня"] == Status.DONE


def reply_to(author_id):
    return {
        "message_id": 5,
        "date": int(FrozenDateTime.now(TZ).timestamp()),
        "chat": {"id": -10042, "type": "supergroup"},
        "from": {"id": author_id, "first_name": "bot", "is_bot": True},
        "text": "Пивной надзор на связи",
    }


async def test_text_reply_to_bot_gets_banter(telegram, repo):
    dp, bot, session, _ = telegram
    await dp.feed_update(
        bot, message_update(text="ну ты и бухгалтер", reply_to_message=reply_to(bot.id))
    )
    replies = [call for call in session.calls if isinstance(call, SendMessage)]
    assert len(replies) == 1 and "нахуй" in replies[0].text
    assert replies[0].reply_parameters.message_id == 10
    assert repo.writes == 0


@pytest.mark.parametrize(
    "case", ["private", "other_chat", "other_author", "bot_sender", "unknown_command"]
)
async def test_banter_is_limited_to_human_replies_in_club(telegram, case):
    dp, bot, session, _ = telegram
    fields = {"text": "привет", "reply_to_message": reply_to(bot.id)}
    if case == "private":
        fields["chat"] = {"id": 101, "type": "private"}
    elif case == "other_chat":
        fields["chat"] = {"id": -999, "type": "group"}
    elif case == "other_author":
        fields["reply_to_message"] = reply_to(456)
    elif case == "bot_sender":
        fields["from"] = {"id": 456, "first_name": "other bot", "is_bot": True}
    else:
        fields.update(text="/unknown", entities=[{"type": "bot_command", "offset": 0, "length": 8}])
    await dp.feed_update(bot, message_update(**fields))
    assert not session.calls


@pytest.mark.parametrize("content", ["command", "video"])
async def test_reply_still_processes_commands_and_video(telegram, repo, content):
    dp, bot, session, _ = telegram
    fields = {"reply_to_message": reply_to(bot.id)}
    if content == "command":
        fields.update(text="/done", entities=[{"type": "bot_command", "offset": 0, "length": 5}])
    else:
        fields["video_note"] = clip()
    await dp.feed_update(bot, message_update(**fields))
    assert repo.days[1].statuses["Саня"] == Status.DONE
    assert all("нахуй" not in call.text for call in session.calls if isinstance(call, SendMessage))


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


@pytest.mark.parametrize("inviter", [101, 718724903])
@pytest.mark.parametrize("chat_type", ["group", "supergroup", "channel"])
@pytest.mark.parametrize("prior", ["left", "kicked"])
async def test_only_owner_invitation_accepted(telegram, inviter, chat_type, prior):
    dp, bot, session, _ = telegram
    update = Update.model_validate(
        {
            "update_id": 20,
            "my_chat_member": {
                "chat": {"id": -999, "type": chat_type},
                "date": int(FrozenDateTime.now(TZ).timestamp()),
                "from": {
                    "id": inviter,
                    "first_name": "Ярик",
                    "is_bot": False,
                    "username": "yarvod",
                },
                "old_chat_member": {
                    "status": prior,
                    "until_date": 0,
                    "user": {"id": bot.id, "first_name": "bot", "is_bot": True},
                },
                "new_chat_member": {
                    "status": "member",
                    "user": {"id": bot.id, "first_name": "bot", "is_bot": True},
                },
            },
        }
    )
    if inviter != 718724903:
        with pytest.raises(AdmissionPending):
            await dp.feed_update(bot, update)
        assert not session.calls
    await dp.feed_update(bot, update, defer_unknown_invitation=False)
    if inviter == 718724903:
        assert not session.calls
    else:
        assert session.calls[0].__api_method__ == "leaveChat"


async def test_other_user_cannot_setup_even_with_owner_username(telegram, repo):
    dp, bot, session, _ = telegram
    with pytest.raises(RuleError):
        await dp.feed_update(
            bot,
            message_update(
                text="/setup",
                entities=[{"type": "bot_command", "offset": 0, "length": 6}],
                **{
                    "from": {"id": 101, "first_name": "Ярик", "is_bot": False, "username": "yarvod"}
                },
            ),
        )
    assert not session.calls
    assert repo.states["chat_id"] == "-10042"


async def test_owner_cannot_setup_anonymously(telegram):
    dp, bot, session, _ = telegram
    with pytest.raises(RuleError):
        await dp.feed_update(
            bot,
            message_update(
                text="/setup",
                entities=[{"type": "bot_command", "offset": 0, "length": 6}],
                sender_chat={"id": -10042, "type": "supergroup"},
                **{"from": {"id": 718724903, "first_name": "Ярик", "is_bot": False}},
            ),
        )
    assert not session.calls


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


def membership_update(bot, *, chat_id, actor=101, old="left", new="member", update_id=20):
    def member(status):
        value = {"status": status, "user": {"id": bot.id, "first_name": "bot", "is_bot": True}}
        if status == "administrator":
            value.update(
                dict.fromkeys(
                    (
                        "can_be_edited",
                        "is_anonymous",
                        "can_manage_chat",
                        "can_delete_messages",
                        "can_manage_video_chats",
                        "can_restrict_members",
                        "can_promote_members",
                        "can_change_info",
                        "can_invite_users",
                        "can_post_stories",
                        "can_edit_stories",
                        "can_delete_stories",
                        "can_send_welcome_messages",
                    ),
                    False,
                )
            )
        if status == "restricted":
            value.update(
                dict.fromkeys(
                    (
                        "can_send_messages",
                        "can_send_audios",
                        "can_send_documents",
                        "can_send_photos",
                        "can_send_videos",
                        "can_send_video_notes",
                        "can_send_voice_notes",
                        "can_send_polls",
                        "can_send_other_messages",
                        "can_add_web_page_previews",
                        "can_change_info",
                        "can_invite_users",
                        "can_pin_messages",
                        "can_manage_topics",
                        "can_react_to_messages",
                        "can_edit_tag",
                    ),
                    False,
                )
            )
            value.update(is_member=True, until_date=0)
        return value

    return Update.model_validate(
        {
            "update_id": update_id,
            "my_chat_member": {
                "chat": {"id": chat_id, "type": "supergroup"},
                "date": int(FrozenDateTime.now(TZ).timestamp()),
                "from": {"id": actor, "first_name": "admin", "is_bot": False},
                "old_chat_member": member(old),
                "new_chat_member": member(new),
            },
        }
    )


@pytest.mark.parametrize(
    "old,new",
    [
        ("member", "administrator"),
        ("member", "restricted"),
        ("restricted", "administrator"),
        ("administrator", "member"),
    ],
)
async def test_other_admin_can_change_bot_permissions(telegram, old, new):
    dp, bot, session, _ = telegram
    await dp.feed_update(bot, membership_update(bot, chat_id=-10042, old=old, new=new))
    assert not session.calls


@pytest.mark.parametrize("already_active", [False, True])
async def test_admin_promotion_migrates_owner_invited_group(telegram, repo, already_active):
    from arq import Retry

    from pullups_bot.worker import process_update

    dp, bot, session, container = telegram
    repo.states.clear()
    await dp.feed_update(bot, membership_update(bot, chat_id=-999, actor=718724903, update_id=10))
    if already_active:
        repo.states["chat_id"] = "-999"
    ctx = {"container": container, "dispatcher": dp, "job_try": 1}
    invitation = membership_update(bot, chat_id=-10099)
    with pytest.raises(Retry):
        await process_update(ctx, invitation.model_dump(mode="json"))
    assert not session.calls

    proof = message_update(chat={"id": -10099, "type": "supergroup"}, migrate_from_chat_id=-999)
    await process_update(ctx, proof.model_dump(mode="json"))
    await dp.feed_update(
        bot, membership_update(bot, chat_id=-10099, old="member", new="restricted")
    )
    await dp.feed_update(
        bot, membership_update(bot, chat_id=-10099, old="restricted", new="administrator")
    )
    ctx["job_try"] = 2
    await process_update(ctx, invitation.model_dump(mode="json"))
    # ARQ may redeliver a completed update after a lost result; it must remain safe.
    await process_update(ctx, invitation.model_dump(mode="json"))
    assert not session.calls
    assert repo.states.get("chat_id") == ("-10099" if already_active else None)

    # Telegram also emits a second migration message; it must not permit a fresh invitation.
    await dp.feed_update(
        bot, message_update(chat={"id": -999, "type": "group"}, migrate_to_chat_id=-10099)
    )
    fresh_invitation = membership_update(bot, chat_id=-10099, update_id=50)
    await process_update(ctx, fresh_invitation.model_dump(mode="json"))
    assert session.calls[-1].__api_method__ == "leaveChat"


async def test_migration_of_unapproved_group_does_not_bypass_owner(telegram, repo):
    dp, bot, session, _ = telegram
    await dp.feed_update(
        bot, message_update(chat={"id": -10099, "type": "supergroup"}, migrate_from_chat_id=-999)
    )
    await dp.feed_update(
        bot,
        membership_update(bot, chat_id=-10099, new="administrator"),
        defer_unknown_invitation=False,
    )
    assert session.calls[-1].__api_method__ == "leaveChat"
    assert repo.states["chat_id"] == "-10042"


async def test_old_migration_does_not_authorize_later_invitation(service):
    await service.remember_invitation(718724903, -999)
    await service.migrate_chat(-999, -10099, 1000)
    assert not await service.accept_migration(-10099, 1060, 20)


async def test_leaving_revokes_migration_permission(service, repo):
    await service.remember_invitation(718724903, -999)
    await service.migrate_chat(-999, -10099, 1000)
    assert await service.accept_migration(-10099, 1000, 20)
    repo.states["chat_id"] = "-10099"
    await service.forget_chat(-10099)
    assert not await service.accept_migration(-10099, 1000, 21)
    assert not repo.states["chat_id"]


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
