import os
from contextlib import asynccontextmanager
from datetime import date, time

import pytest

from pullups_bot.application.service import ClubService
from pullups_bot.config import Settings
from pullups_bot.domain.models import Day, Member, Snapshot, Status

# Tests never need the real bot token or network credentials.
os.environ["TOKEN"] = "123456:TEST_TOKEN_FOR_OFFLINE_TESTS_ONLY"


class MemoryRepository:
    def __init__(self):
        self.members = [
            Member("Саня", "weebat", 3),
            Member("Ярик", "yarvod", 4, 3),
            Member("Тёма", "atep_art", 5, 2),
            Member("Лёша", "pukenzo", 6),
        ]
        self.days = [
            Day(date(2026, 9, n), n + 12, {m.name: Status.PENDING for m in self.members})
            for n in (5, 6, 7)
        ]
        self.states = {"chat_id": "-10042", "started_on": "2026-09-06"}
        self.events = set()
        self.writes = 0
        self.marks = []

    async def snapshot(self):
        return Snapshot(self.members, self.days)

    async def state(self, key):
        return self.states.get(key)

    async def save_state(self, key, value):
        self.states[key] = value

    async def event_exists(self, event_id):
        return event_id in self.events

    async def mark(self, day, member, status, event_id, actor_id, reason, evidence):
        if event_id in self.events:
            return False
        self.events.add(event_id)
        self.writes += 1
        self.marks.append((day.date, member.name, status, reason))
        previous = day.statuses[member.name]
        day.statuses[member.name] = status
        return previous != status

    async def close_day(self, day, event_id):
        for name, status in day.statuses.items():
            if status == Status.PENDING:
                day.statuses[name] = Status.MISSED
        self.events.add(event_id)

    async def ensure_dates(self, through):
        pass


class MemoryMutex:
    @asynccontextmanager
    async def hold(self):
        yield


class MemoryPrompts:
    def __init__(self):
        self.items = {}

    async def save(self, chat_id, message_id, prompt):
        self.items[chat_id, message_id] = prompt

    async def get(self, chat_id, message_id):
        return self.items.get((chat_id, message_id))

    async def delete(self, chat_id, message_id):
        self.items.pop((chat_id, message_id), None)


@pytest.fixture
def prompts():
    return MemoryPrompts()


@pytest.fixture
def repo():
    return MemoryRepository()


@pytest.fixture
def settings():
    return Settings(_env_file=None, token=os.environ["TOKEN"])


@pytest.fixture
def service(repo):
    return ClubService(repo, MemoryMutex(), 718724903, time(23, 59))
