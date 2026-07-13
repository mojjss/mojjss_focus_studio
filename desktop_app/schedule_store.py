from __future__ import annotations

import csv
import os
import re
import threading
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable


FIELDS = (
    "id", "date", "start", "end", "title", "category", "notes",
    "updated_at", "deleted", "source",
)
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class ScheduleValidationError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp_is_newer(left: str, right: str) -> bool:
    try:
        left_dt = datetime.fromisoformat(str(left).replace("Z", "+00:00"))
        right_dt = datetime.fromisoformat(str(right).replace("Z", "+00:00"))
        if left_dt.tzinfo is None:
            left_dt = left_dt.replace(tzinfo=timezone.utc)
        if right_dt.tzinfo is None:
            right_dt = right_dt.replace(tzinfo=timezone.utc)
        return left_dt.astimezone(timezone.utc) > right_dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return str(left) > str(right)


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
    """Atomic CSV schedule store with sync metadata and deletion tombstones."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate_or_initialize()

    @staticmethod
    def _normalise_raw(raw: dict[str, Any]) -> dict[str, str] | None:
        raw_date = str(raw.get("date", "")).strip()
        if not raw_date:
            return None
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
            return None
        clean.update(
            {
                "id": str(raw.get("id", "")).strip() or uuid.uuid4().hex,
                "updated_at": str(raw.get("updated_at", "")).strip() or utc_now(),
                "deleted": "1" if str(raw.get("deleted", "0")).strip() in {"1", "true", "True"} else "0",
                "source": str(raw.get("source", "desktop")).strip() or "desktop",
            }
        )
        return clean

    def _migrate_or_initialize(self) -> None:
        if not self.path.exists():
            self._write_rows([])
            return
        migrated: list[dict[str, str]] = []
        try:
            with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
                for raw in csv.DictReader(handle):
                    clean = self._normalise_raw(raw)
                    if clean is not None:
                        migrated.append(clean)
        except (OSError, csv.Error):
            migrated = []
        self._write_rows(self._sorted(migrated))

    def _read_rows(self, *, include_deleted: bool = True) -> list[dict[str, str]]:
        with self._lock:
            if not self.path.exists():
                return []
            try:
                rows: list[dict[str, str]] = []
                with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
                    for raw in csv.DictReader(handle):
                        clean = self._normalise_raw(raw)
                        if clean is None:
                            continue
                        if not include_deleted and clean["deleted"] == "1":
                            continue
                        rows.append(clean)
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
                row.get("date", ""), row.get("start", ""), row.get("end", ""),
                row.get("title", "").casefold(), row.get("id", ""),
            ),
        )

    def events_between(self, start_date: date, end_date: date) -> list[dict[str, str]]:
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        start_text, end_text = start_date.isoformat(), end_date.isoformat()
        rows = [
            row for row in self._read_rows(include_deleted=False)
            if start_text <= row["date"] <= end_text
        ]
        return self._sorted(rows)

    def events_on(self, event_date: date) -> list[dict[str, str]]:
        return self.events_between(event_date, event_date)

    def get(self, event_id: str) -> dict[str, str] | None:
        for row in self._read_rows(include_deleted=False):
            if row["id"] == event_id:
                return row
        return None

    def add(self, *, event_date: str, start: str, end: str, title: str,
            category: str, notes: str = "") -> dict[str, str]:
        clean = validate_event(
            event_date=event_date, start=start, end=end, title=title,
            category=category, notes=notes,
        )
        clean.update({
            "id": uuid.uuid4().hex,
            "updated_at": utc_now(),
            "deleted": "0",
            "source": "desktop",
        })
        rows = self._read_rows()
        rows.append(clean)
        self._write_rows(self._sorted(rows))
        return clean

    def update(self, event_id: str, *, event_date: str, start: str, end: str,
               title: str, category: str, notes: str = "") -> bool:
        clean = validate_event(
            event_date=event_date, start=start, end=end, title=title,
            category=category, notes=notes,
        )
        rows = self._read_rows()
        changed = False
        for index, row in enumerate(rows):
            if row["id"] == event_id:
                rows[index] = {
                    "id": event_id, **clean,
                    "updated_at": utc_now(), "deleted": "0", "source": "desktop",
                }
                changed = True
                break
        if changed:
            self._write_rows(self._sorted(rows))
        return changed

    def delete(self, event_ids: Iterable[str]) -> int:
        ids = set(event_ids)
        rows = self._read_rows()
        changed = 0
        stamp = utc_now()
        for row in rows:
            if row["id"] in ids and row.get("deleted") != "1":
                row["deleted"] = "1"
                row["updated_at"] = stamp
                row["source"] = "desktop"
                changed += 1
        if changed:
            self._write_rows(self._sorted(rows))
        return changed

    def all_sync_rows(
        self,
        *,
        updated_after: str | None = None,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in self._read_rows(include_deleted=True):
            if updated_after and not timestamp_is_newer(
                row.get("updated_at", ""), updated_after
            ):
                continue
            item: dict[str, Any] = dict(row)
            item["deleted"] = row.get("deleted") == "1"
            result.append(item)
        return result

    def merge_remote(self, remote_rows: Iterable[dict[str, Any]]) -> int:
        local = {row["id"]: row for row in self._read_rows(include_deleted=True)}
        changed = 0
        for raw in remote_rows:
            clean = self._normalise_raw(raw)
            if clean is None:
                continue
            existing = local.get(clean["id"])
            if existing is None or timestamp_is_newer(
                clean["updated_at"], existing.get("updated_at", "")
            ):
                local[clean["id"]] = clean
                changed += 1
        if changed:
            self._write_rows(self._sorted(list(local.values())))
        return changed

    def distinct_categories(self) -> list[str]:
        return sorted(
            {row["category"] for row in self._read_rows(include_deleted=False) if row["category"]},
            key=str.casefold,
        )

    def export(self, destination: Path) -> int:
        rows = self._sorted(self._read_rows(include_deleted=False))
        destination.parent.mkdir(parents=True, exist_ok=True)
        public_fields = ("id", "date", "start", "end", "title", "category", "notes")
        with destination.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=public_fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in public_fields})
        return len(rows)
