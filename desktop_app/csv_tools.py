from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


REQUIRED_COLUMNS = {
    "task",
    "category",
    "mode",
    "started_at",
    "ended_at",
    "minutes",
}


class CsvImportError(ValueError):
    pass


def _parse_datetime(value: str, zone: ZoneInfo, row_number: int, field: str) -> datetime:
    text = value.strip()
    if not text:
        raise CsvImportError(f"Row {row_number}: {field} is empty.")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CsvImportError(
            f"Row {row_number}: {field} must be an ISO date/time, for example "
            "2026-07-01T18:30:00+03:30."
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed


def _parse_int(value: str, row_number: int, field: str, default: int = 0) -> int:
    text = value.strip()
    if not text:
        return default
    try:
        number = int(float(text))
    except ValueError as exc:
        raise CsvImportError(f"Row {row_number}: {field} must be a number.") from exc
    return number


def read_session_csv(path: Path, timezone_name: str) -> list[dict[str, Any]]:
    zone = ZoneInfo(timezone_name)
    try:
        handle = path.open("r", newline="", encoding="utf-8-sig")
    except OSError as exc:
        raise CsvImportError(str(exc)) from exc

    with handle:
        reader = csv.DictReader(handle)
        headers = set(reader.fieldnames or [])
        missing = sorted(REQUIRED_COLUMNS - headers)
        if missing:
            raise CsvImportError("Missing columns: " + ", ".join(missing))

        parsed_rows: list[dict[str, Any]] = []
        for row_number, row in enumerate(reader, start=2):
            started = _parse_datetime(row.get("started_at", ""), zone, row_number, "started_at")
            ended = _parse_datetime(row.get("ended_at", ""), zone, row_number, "ended_at")
            if ended < started:
                raise CsvImportError(f"Row {row_number}: ended_at is before started_at.")

            minutes = _parse_int(row.get("minutes", ""), row_number, "minutes")
            if minutes < 1:
                raise CsvImportError(f"Row {row_number}: minutes must be at least 1.")

            raw_mode = (row.get("mode") or "Focus").strip().lower()
            mode_map = {
                "focus": "Focus",
                "flow": "Flow",
                "productive": "Productive",
                "productive work": "Productive",
                "personal": "Personal",
                "short break": "Short Break",
                "long break": "Long Break",
            }
            if raw_mode not in mode_map:
                raise CsvImportError(
                    f"Row {row_number}: mode must be Focus, Flow, Productive, "
                    "Personal, Short Break, or Long Break."
                )
            mode = mode_map[raw_mode]

            default_focus = 1 if mode in {"Focus", "Flow"} else 0
            focus_value = _parse_int(
                row.get("counts_toward_focus", str(default_focus)),
                row_number,
                "counts_toward_focus",
                default_focus,
            )

            completed = _parse_int(row.get("completed", "1"), row_number, "completed", 1)
            values = {
                "task": (row.get("task") or "Untitled focus session").strip(),
                "category": (row.get("category") or "General").strip(),
                "mode": mode,
                "counts_toward_focus": 1 if focus_value else 0,
                "started_at_utc": started.astimezone(timezone.utc).isoformat(),
                "ended_at_utc": ended.astimezone(timezone.utc).isoformat(),
                "local_date": (
                    (row.get("local_date") or "").strip()
                    or ended.astimezone(zone).strftime("%Y%m%d")
                ),
                "planned_minutes": max(
                    0,
                    _parse_int(
                        row.get("planned_minutes", ""),
                        row_number,
                        "planned_minutes",
                        minutes,
                    ),
                ),
                "minutes": minutes,
                "notes": (row.get("notes") or "").strip(),
                "completed": 1 if completed else 0,
                "source": (row.get("source") or "csv-import").strip(),
                "synced": 0 if focus_value else 1,
            }
            parsed_rows.append(values)

    if not parsed_rows:
        raise CsvImportError("The CSV file contains no session rows.")
    return parsed_rows
