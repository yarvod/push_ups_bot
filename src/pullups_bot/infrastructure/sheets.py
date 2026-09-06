import asyncio
import time as clock
from datetime import UTC, date, datetime, timedelta
from typing import Any

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials

from pullups_bot.config import Settings
from pullups_bot.domain.models import Day, Member, Snapshot, Status

DAILY = "Ежедневно"
SUMMARY = "Сводка"
STATE = "Бот — настройки"
JOURNAL = "Бот — журнал"
EPOCH = date(1899, 12, 30)


def value_cell(value: str | int | float) -> dict:
    key = "numberValue" if isinstance(value, (int, float)) else "stringValue"
    return {"userEnteredValue": {key: value}}


def cell_update(
    sheet_id: int, row: int, column: int, value: str | int | float, note: str | None = None
) -> dict:
    cell = value_cell(value)
    if note is not None:
        cell["note"] = note
    return {
        "updateCells": {
            "start": {"sheetId": sheet_id, "rowIndex": row - 1, "columnIndex": column - 1},
            "rows": [{"values": [cell]}],
            "fields": "userEnteredValue,note" if note is not None else "userEnteredValue",
        }
    }


class SheetsError(Exception):
    """Sanitized infrastructure error; never include auth headers or request bodies."""


class SheetsRepository:
    def __init__(self, settings: Settings):
        self.settings = settings
        credentials = Credentials.from_service_account_file(
            settings.google_application_credentials,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        self.session = AuthorizedSession(credentials)
        self.base = f"https://sheets.googleapis.com/v4/spreadsheets/{settings.spreadsheet_id}"
        self.tabs: dict[str, dict] = {}
        self._states: dict[str, tuple[int, str]] = {}
        self._states_at = 0.0
        self._events: set[str] = set()
        self._events_at = 0.0

    async def close(self) -> None:
        await asyncio.to_thread(self.session.close)

    async def request(self, method: str, path: str = "", **kwargs: Any) -> dict:
        def perform() -> dict:
            try:
                response = self.session.request(method, self.base + path, timeout=25, **kwargs)
            except Exception as exc:
                raise SheetsError(f"Google Sheets transport: {type(exc).__name__}") from None
            if not response.ok:
                raise SheetsError(f"Google Sheets HTTP {response.status_code}")
            return response.json()

        operation = asyncio.create_task(asyncio.to_thread(perform))
        try:
            return await asyncio.shield(operation)
        except asyncio.CancelledError:
            # Keep the write lock until the HTTP write running in a thread has finished.
            await operation
            raise

    async def metadata(self) -> dict:
        data = await self.request("GET", params={"fields": "properties,sheets.properties"})
        self.tabs = {s["properties"]["title"]: s["properties"] for s in data["sheets"]}
        return data

    async def values(self, ranges: list[str], render: str = "FORMATTED_VALUE") -> list[list]:
        data = await self.request(
            "GET",
            "/values:batchGet",
            params={
                "ranges": ranges,
                "valueRenderOption": render,
            },
        )
        return [r.get("values", []) for r in data.get("valueRanges", [])]

    async def batch(self, requests: list[dict]) -> None:
        if requests:
            await self.request("POST", ":batchUpdate", json={"requests": requests})

    async def initialize(self) -> None:
        await self.metadata()
        if DAILY not in self.tabs or SUMMARY not in self.tabs:
            raise SheetsError("Expected sheets Ежедневно and Сводка are missing")
        headers = {
            STATE: ["Ключ", "Значение"],
            JOURNAL: [
                "ID события",
                "Время UTC",
                "Дата",
                "Участник",
                "Было",
                "Стало",
                "Telegram ID автора",
                "Причина",
                "Видео / сообщение",
            ],
        }
        for title, columns in headers.items():
            if title not in self.tabs:
                await self.batch(
                    [
                        {
                            "addSheet": {
                                "properties": {
                                    "title": title,
                                    "gridProperties": {
                                        "rowCount": 1000,
                                        "columnCount": len(columns),
                                        "frozenRowCount": 1,
                                    },
                                }
                            }
                        }
                    ]
                )
                await self.metadata()
                sid = self.tabs[title]["sheetId"]
                await self.batch(
                    [
                        {
                            "updateCells": {
                                "start": {"sheetId": sid, "rowIndex": 0, "columnIndex": 0},
                                "rows": [{"values": [value_cell(c) for c in columns]}],
                                "fields": "userEnteredValue",
                            },
                        },
                        {
                            "repeatCell": {
                                "range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
                                "cell": {
                                    "userEnteredFormat": {
                                        "textFormat": {"bold": True},
                                        "backgroundColor": {
                                            "red": 0.85,
                                            "green": 0.93,
                                            "blue": 0.88,
                                        },
                                    }
                                },
                                "fields": "userEnteredFormat",
                            }
                        },
                        {
                            "updateDimensionProperties": {
                                "range": {
                                    "sheetId": sid,
                                    "dimension": "COLUMNS",
                                    "startIndex": 0,
                                    "endIndex": len(columns),
                                },
                                "properties": {"pixelSize": 190},
                                "fields": "pixelSize",
                            }
                        },
                    ]
                )
        await self.sync_rules()

    async def snapshot(self) -> Snapshot:
        if not self.tabs:
            await self.metadata()
        count = self.tabs[DAILY]["gridProperties"]["rowCount"]
        daily, summary = await self.values([f"'{DAILY}'!A3:I{count}", f"'{SUMMARY}'!A4:B30"])
        if not daily or daily[0][:2] != ["Дата", "День"]:
            raise SheetsError("Daily sheet header changed")
        names = daily[0][2:6]
        if len(names) != 4 or len(set(names)) != 4:
            raise SheetsError("Expected four unique member columns C:F")
        mapping_start = next((i for i, r in enumerate(summary) if r and r[0] == "Пользователи"), -1)
        if mapping_start < 0:
            raise SheetsError("Username mapping Пользователи not found")
        mapping = {r[0]: str(r[1]).lstrip("@") for r in summary[mapping_start + 1 :] if len(r) > 1}
        initial = {r[0]: int(r[1]) for r in summary[1:5] if len(r) > 1}
        if any(name not in mapping or name not in initial for name in names):
            raise SheetsError("Member names differ between daily, summary and username mapping")
        if len({mapping[n].casefold() for n in names}) != 4:
            raise SheetsError("Duplicate Telegram usernames")
        if any(initial[n] < 0 for n in names):
            raise SheetsError("Initial misses cannot be negative")
        members = [Member(n, mapping[n], col, initial[n]) for col, n in enumerate(names, 3)]
        days = []
        for rownum, row in enumerate(daily[1:], 4):
            if not row or not row[0]:
                continue
            try:
                target = datetime.strptime(row[0], "%d.%m.%Y").date()
                statuses = {
                    m.name: Status(row[m.column - 1] if len(row) >= m.column else "")
                    for m in members
                }
            except ValueError:
                raise SheetsError(f"Unknown date/status in daily row {rownum}") from None
            days.append(Day(target, rownum, statuses))
        if len({d.date for d in days}) != len(days):
            raise SheetsError("Duplicate dates in daily sheet")
        return Snapshot(members, days)

    async def _load_states(self) -> None:
        if clock.monotonic() - self._states_at < 5:
            return
        (rows,) = await self.values([f"'{STATE}'!A2:B1000"])
        self._states = {
            str(r[0]): (i, str(r[1]) if len(r) > 1 else "") for i, r in enumerate(rows, 2) if r
        }
        self._states_at = clock.monotonic()

    async def state(self, key: str) -> str | None:
        await self._load_states()
        return self._states.get(key, (0, ""))[1] or None

    async def save_state(self, key: str, value: str) -> None:
        await self._load_states()
        sid = self.tabs[STATE]["sheetId"]
        row = self._states.get(
            key, (max((v[0] for v in self._states.values()), default=1) + 1, "")
        )[0]
        await self.batch([cell_update(sid, row, 1, key), cell_update(sid, row, 2, value)])
        self._states[key] = (row, value)

    async def event_exists(self, event_id: str) -> bool:
        if event_id in self._events:
            return True
        # Never cache a negative result: a timed-out batch may have committed at Google.
        (rows,) = await self.values([f"'{JOURNAL}'!A2:A"])
        self._events = {str(r[0]) for r in rows if r}
        self._events_at = clock.monotonic()
        return event_id in self._events

    def journal(self, fields: list[str]) -> dict:
        return {
            "appendCells": {
                "sheetId": self.tabs[JOURNAL]["sheetId"],
                "rows": [{"values": [value_cell(f) for f in fields]}],
                "fields": "userEnteredValue",
            }
        }

    async def mark(
        self,
        day: Day,
        member: Member,
        status: Status,
        event_id: str,
        actor_id: int,
        reason: str,
        evidence: str,
    ) -> bool:
        if await self.event_exists(event_id):
            return False
        before = day.statuses[member.name]
        note = f"Бот: {datetime.now(UTC).isoformat()}\nАвтор: {actor_id}\n{reason}\n{evidence}"
        # A single Google batch commits the status and its idempotency/audit record atomically.
        await self.batch(
            [
                cell_update(
                    self.tabs[DAILY]["sheetId"], day.row, member.column, status.value, note
                ),
                self.journal(
                    [
                        event_id,
                        datetime.now(UTC).isoformat(),
                        day.date.isoformat(),
                        member.name,
                        before.value,
                        status.value,
                        str(actor_id),
                        reason,
                        evidence,
                    ]
                ),
            ]
        )
        self._events.add(event_id)
        return before != status

    async def close_day(self, day: Day, event_id: str) -> None:
        snapshot = await self.snapshot()
        fresh = snapshot.day(day.date)
        requests = [
            cell_update(
                self.tabs[DAILY]["sheetId"],
                fresh.row,
                m.column,
                Status.MISSED.value,
                "Нет отметки до дедлайна; закрыто ботом.",
            )
            for m in snapshot.members
            if fresh.statuses[m.name] == Status.PENDING
        ]
        requests.append(
            self.journal(
                [
                    event_id,
                    datetime.now(UTC).isoformat(),
                    day.date.isoformat(),
                    "Все",
                    "",
                    "День закрыт",
                    "0",
                    "Автоматически",
                    "",
                ]
            )
        )
        await self.batch(requests)
        self._events.add(event_id)

    async def ensure_dates(self, through: date) -> None:
        snapshot = await self.snapshot()
        last = max(snapshot.days, key=lambda d: d.date)
        if last.date >= through:
            return
        count = (through - last.date).days
        sid = self.tabs[DAILY]["sheetId"]
        end = last.row + count
        requests = []
        if end > self.tabs[DAILY]["gridProperties"]["rowCount"]:
            requests.append(
                {"appendDimension": {"sheetId": sid, "dimension": "ROWS", "length": count + 100}}
            )
        source = {
            "sheetId": sid,
            "startRowIndex": last.row - 1,
            "endRowIndex": last.row,
            "startColumnIndex": 0,
            "endColumnIndex": 9,
        }
        destination = {**source, "startRowIndex": last.row, "endRowIndex": end}
        for kind in ("PASTE_FORMAT", "PASTE_DATA_VALIDATION", "PASTE_FORMULA"):
            requests.append(
                {"copyPaste": {"source": source, "destination": destination, "pasteType": kind}}
            )
        for offset in range(1, count + 1):
            target = last.date + timedelta(days=offset)
            row = last.row + offset
            requests.extend(
                [
                    cell_update(sid, row, 1, (target - EPOCH).days),
                    cell_update(
                        sid, row, 2, ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")[target.weekday()]
                    ),
                ]
            )
            requests.extend(cell_update(sid, row, col, "") for col in (3, 4, 5, 6, 9))
        await self.batch(requests)
        await self.metadata()

    async def sync_rules(self) -> None:
        """Align existing formulas with .env; preserve input cells and all formatting."""
        config = self.settings
        fingerprint = (
            f"v3:{config.timezone}:{config.deadline_time}:"
            f"{config.reminder_time}:{config.summary_time}"
        )
        if await self.state("rules_version") == fingerprint:
            return
        cutoff = f"TIME({config.deadline_time.hour};{config.deadline_time.minute};0)+1/1440"
        daily_id, summary_id = self.tabs[DAILY]["sheetId"], self.tabs[SUMMARY]["sheetId"]
        snapshot = await self.snapshot()
        requests: list[dict] = [
            {
                "updateSpreadsheetProperties": {
                    "properties": {
                        "timeZone": config.timezone,
                        "autoRecalc": "MINUTE",
                        "title": f"Отжимания до {config.deadline_time:%H:%M} — пивной учёт",
                    },
                    "fields": "timeZone,autoRecalc,title",
                }
            }
        ]

        def formula(sid: int, row: int, col: int, text: str) -> dict:
            req = cell_update(sid, row, col, "")
            req["updateCells"]["rows"][0]["values"][0] = {
                "userEnteredValue": {"formulaValue": text}
            }
            return req

        # This existing workbook uses ru_RU: semicolons and English function names.
        for day in snapshot.days:
            r = day.row
            f = (
                f'=IF(A{r}>TODAY();"";COUNTIF(C{r}:F{r};"❌ Проебал")'
                f"+IF(NOW()>=A{r}+{cutoff};COUNTBLANK(C{r}:F{r});0))"
            )
            requests.append(formula(daily_id, r, 7, f))
        for index, member in enumerate(snapshot.members, 5):
            col = chr(64 + member.column)
            dates = f"'{DAILY}'!$A$4:$A"
            statuses = f"'{DAILY}'!${col}$4:${col}"
            f = (
                f'=COUNTIFS({dates};"<="&TODAY();{statuses};"❌ Проебал")'
                f'+COUNTIFS({dates};"<="&(NOW()-({cutoff}));{dates};">0";{statuses};"")'
            )
            requests.append(formula(summary_id, index, 3, f))
        deadline = f"{config.deadline_time:%H:%M} ({config.timezone})"
        requests.extend(
            [
                cell_update(daily_id, 1, 1, f"Отжимания до {deadline} 🍺"),
                cell_update(
                    daily_id,
                    2,
                    1,
                    "✅ Отжался / 🟡 Уважительная / ❌ Проебал. "
                    f"Пустая отметка после {deadline} = пропуск. "
                    "За пропуск — по пиву каждому; взаимозачёта нет.",
                ),
                cell_update(
                    summary_id,
                    2,
                    1,
                    f"Учёт с 23.08.2026. Дедлайн {deadline}. Пропуск = по 1 пиву каждому. "
                    "Уважительная причина без штрафа. Стартовые штрафные сохранены; "
                    "встречные долги не вычитаются.",
                ),
                cell_update(
                    summary_id,
                    19,
                    2,
                    f"До {deadline} поставить отметку. После дедлайна пустая считается пропуском.",
                ),
            ]
        )
        await self.batch(requests)
        for key, value in {
            "timezone": config.timezone,
            "deadline_time": config.deadline_time.strftime("%H:%M"),
            "reminder_time": config.reminder_time.strftime("%H:%M"),
            "summary_time": config.summary_time.strftime("%H:%M"),
            "rules_version": fingerprint,
        }.items():
            await self.save_state(key, value)
