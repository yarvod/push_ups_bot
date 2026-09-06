"""Explicit operator actions; never prints credentials or Telegram request URLs."""

import argparse
import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

from aiogram import Bot

from pullups_bot.config import Settings
from pullups_bot.infrastructure.sheets import SheetsRepository


async def run(action: str) -> None:
    settings = Settings()
    repository = SheetsRepository(settings)
    try:
        if action == "backup":
            data = await repository.request("GET", params={"includeGridData": "true"})
            folder = Path("backups")
            await asyncio.to_thread(folder.mkdir, mode=0o700, exist_ok=True)
            path = folder / f"sheets-{datetime.now():%Y%m%d-%H%M%S}.json"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as output:
                json.dump(data, output, ensure_ascii=False)
            print(f"Backup saved: {path}")
        elif action == "init":
            await repository.initialize()
            print("Google Sheets initialized; rules synchronized; participant marks preserved")
        elif action == "doctor":
            await repository.metadata()
            snapshot = await repository.snapshot()
            print(f"Google Sheets OK: {len(snapshot.members)} members, {len(snapshot.days)} dates")
            async with Bot(settings.token.get_secret_value()) as bot:
                user = await bot.get_me()
                print(
                    f"Telegram OK: @{user.username}; "
                    f"group privacy disabled={bool(user.can_read_all_group_messages)}"
                )
            print(
                f"Timezone={settings.timezone}; reminder={settings.reminder_time}; "
                f"summary={settings.summary_time}; deadline={settings.deadline_time}"
            )
    finally:
        await repository.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["backup", "init", "doctor"])
    try:
        asyncio.run(run(parser.parse_args().action))
    except Exception as exc:
        print(f"Operation failed: {type(exc).__name__}; check connectivity and access")
        raise SystemExit(1) from None
