from __future__ import annotations

import csv
import os
import re
import threading
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable


FIELDS = ("id", "date", "start", "end", "title", "category", "notes")
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class ScheduleValidationError(ValueError):
    pass


def validate_date(value: str) -> str:
    value = value.strip()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ScheduleValidationError(
            "Date must use YYYY-MM-DD, for example 2026-07-06."
        ) from exc


def validate_time(value: str, field_name: str) -> str:
    value = value.strip()
    if not TIME_RE.fullmatch(value):
        raise ScheduleValidationError(
            f"{field_name} must use 24-hour HH:MM format, for example 09:30."
        )
    return value


def validate_event(
    *,
    event_date: str,
    start: str,
    end: str,
    title: str,
    category: str,
    notes: str = "",
) -> dict[str, str]:
    event_date = validate_date(event_date)
    start = validate_time(start, "Start time")
    end = validate_time(end, "End time")
    if end <= start:
        raise ScheduleValidationError("End time must be later than start time.")
    title = title.strip()
    if not title:
        raise ScheduleValidationError("Enter an event title.")
    return {
        "date": event_date,
        "start": start,
        "end": end,
        "title": title,
        "category": category.strip() or "General",
        "notes": notes.strip(),
    }


class ScheduleStore:
    """Small atomic CSV store for date-specific schedule events.

    Blank-date legacy rows are intentionally discarded. This keeps every day
    empty unless the user explicitly adds an event for that date.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate_or_initialize()

    def _migrate_or_initialize(self) -> None:
        if not self.path.exists():
            self._write_rows([])
            return

        migrated: list[dict[str, str]] = []
        try:
            with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                for raw in reader:
                    # Ignore the old bundled repeating examples and every
                    # undated row. New schedules are always date-specific.
                    raw_date = str(raw.get("date", "")).strip()
                    if not raw_date:
                        continue
                    try:
                        clean = validate_event(
                            event_date=raw_date,
                            start=str(raw.get("start", "")),
                            end=str(raw.get("end", "")),
                            title=str(raw.get("title", "")),
                            category=str(raw.get("category", "")),
                            notes=str(raw.get("notes", "")),
                        )
                    except ScheduleValidationError:
                        continue
                    clean["id"] = str(raw.get("id", "")).strip() or uuid.uuid4().hex
                    migrated.append(clean)
        except (OSError, csv.Error):
            migrated = []

        self._write_rows(migrated)

    def _read_rows(self) -> list[dict[str, str]]:
        with self._lock:
            if not self.path.exists():
                return []
            try:
                with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
                    rows = []
                    for raw in csv.DictReader(handle):
                        if not raw.get("id") or not raw.get("date"):
                            continue
                        rows.append({field: str(raw.get(field, "")) for field in FIELDS})
                    return rows
            except (OSError, csv.Error):
                return []

    def _write_rows(self, rows: Iterable[dict[str, Any]]) -> None:
        with self._lock:
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                writer.writeheader()
                for row in rows:
                    writer.writerow({field: str(row.get(field, "")) for field in FIELDS})
            os.replace(temporary, self.path)

    @staticmethod
    def _sorted(rows: list[dict[str, str]]) -> list[dict[str, str]]:
        return sorted(
            rows,
            key=lambda row: (
                row["date"],
                row["start"],
                row["end"],
                row["title"].casefold(),
            ),
        )

    def events_between(
        self,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, str]]:
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        start_text = start_date.isoformat()
        end_text = end_date.isoformat()
        rows = [
            row
            for row in self._read_rows()
            if start_text <= row["date"] <= end_text
        ]
        return self._sorted(rows)

    def events_on(self, event_date: date) -> list[dict[str, str]]:
        return self.events_between(event_date, event_date)

    def get(self, event_id: str) -> dict[str, str] | None:
        for row in self._read_rows():
            if row["id"] == event_id:
                return row
        return None

    def add(
        self,
        *,
        event_date: str,
        start: str,
        end: str,
        title: str,
        category: str,
        notes: str = "",
    ) -> dict[str, str]:
        clean = validate_event(
            event_date=event_date,
            start=start,
            end=end,
            title=title,
            category=category,
            notes=notes,
        )
        clean["id"] = uuid.uuid4().hex
        rows = self._read_rows()
        rows.append(clean)
        self._write_rows(self._sorted(rows))
        return clean

    def update(
        self,
        event_id: str,
        *,
        event_date: str,
        start: str,
        end: str,
        title: str,
        category: str,
        notes: str = "",
    ) -> bool:
        clean = validate_event(
            event_date=event_date,
            start=start,
            end=end,
            title=title,
            category=category,
            notes=notes,
        )
        rows = self._read_rows()
        changed = False
        for index, row in enumerate(rows):
            if row["id"] == event_id:
                rows[index] = {"id": event_id, **clean}
                changed = True
                break
        if changed:
            self._write_rows(self._sorted(rows))
        return changed

    def delete(self, event_ids: Iterable[str]) -> int:
        ids = set(event_ids)
        rows = self._read_rows()
        kept = [row for row in rows if row["id"] not in ids]
        changed = len(rows) - len(kept)
        if changed:
            self._write_rows(kept)
        return changed

    def distinct_categories(self) -> list[str]:
        return sorted(
            {row["category"] for row in self._read_rows() if row["category"]},
            key=str.casefold,
        )

    def export(self, destination: Path) -> int:
        rows = self._sorted(self._read_rows())
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        return len(rows)
