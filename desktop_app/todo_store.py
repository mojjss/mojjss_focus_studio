from __future__ import annotations

import csv
import os
import tempfile
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any


TODO_FIELDS = [
    "id",
    "title",
    "category",
    "notes",
    "priority",
    "estimate_minutes",
    "due_date",
    "scheduled_date",
    "scheduled_start",
    "schedule_event_id",
    "status",
    "created_at",
    "updated_at",
    "completed_at",
]

PRIORITIES = ("Low", "Normal", "High", "Urgent")
STATUSES = ("open", "done")


class TodoValidationError(ValueError):
    pass


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _validate_date(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise TodoValidationError(f"{field_name} must use YYYY-MM-DD.") from exc
    return text


def _validate_time(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        datetime.strptime(text, "%H:%M")
    except ValueError as exc:
        raise TodoValidationError("Scheduled start must use HH:MM.") from exc
    return text


def _normalise(row: dict[str, Any]) -> dict[str, str]:
    result = {field: str(row.get(field, "") or "") for field in TODO_FIELDS}
    result["title"] = result["title"].strip()
    result["category"] = result["category"].strip() or "General"
    result["notes"] = result["notes"].strip()
    result["priority"] = result["priority"].strip().title() or "Normal"
    if result["priority"] not in PRIORITIES:
        result["priority"] = "Normal"
    result["status"] = result["status"].strip().lower() or "open"
    if result["status"] not in STATUSES:
        result["status"] = "open"
    try:
        minutes = int(float(result["estimate_minutes"] or 25))
    except (TypeError, ValueError):
        minutes = 25
    result["estimate_minutes"] = str(max(5, min(1440, minutes)))
    return result


class TodoStore:
    """Small local-first CSV task store with atomic writes."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write([])

    def _read(self) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [_normalise(dict(row)) for row in csv.DictReader(handle)]

    def _write(self, rows: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=self.path.name + ".",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=TODO_FIELDS)
                writer.writeheader()
                for row in rows:
                    writer.writerow(_normalise(dict(row)))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def list(self, *, status: str | None = None) -> list[dict[str, str]]:
        rows = self._read()
        if status:
            rows = [row for row in rows if row["status"] == status]
        priority_rank = {"Urgent": 0, "High": 1, "Normal": 2, "Low": 3}
        rows.sort(
            key=lambda row: (
                row["status"] == "done",
                row["scheduled_date"] or "9999-12-31",
                row["due_date"] or "9999-12-31",
                priority_rank.get(row["priority"], 2),
                row["created_at"],
            )
        )
        return rows

    def get(self, task_id: str) -> dict[str, str] | None:
        wanted = str(task_id or "").strip()
        for row in self._read():
            if row["id"] == wanted:
                return row
        return None

    def add(self, values: dict[str, Any]) -> dict[str, str]:
        row = self._validated(values)
        now = _now_text()
        row.update(
            {
                "id": uuid.uuid4().hex,
                "status": "open",
                "created_at": now,
                "updated_at": now,
                "completed_at": "",
            }
        )
        rows = self._read()
        rows.append(row)
        self._write(rows)
        return _normalise(row)

    def update(self, task_id: str, values: dict[str, Any]) -> dict[str, str]:
        rows = self._read()
        for index, existing in enumerate(rows):
            if existing["id"] != task_id:
                continue
            updated = dict(existing)
            updated.update(self._validated(values))
            updated["id"] = existing["id"]
            updated["created_at"] = existing["created_at"]
            updated["status"] = existing["status"]
            updated["completed_at"] = existing["completed_at"]
            updated["updated_at"] = _now_text()
            rows[index] = updated
            self._write(rows)
            return _normalise(updated)
        raise KeyError("Task not found.")

    def complete(self, task_id: str) -> dict[str, str]:
        return self._set_status(task_id, "done")

    def reopen(self, task_id: str) -> dict[str, str]:
        return self._set_status(task_id, "open")

    def _set_status(self, task_id: str, status: str) -> dict[str, str]:
        rows = self._read()
        for index, existing in enumerate(rows):
            if existing["id"] != task_id:
                continue
            existing["status"] = status
            existing["completed_at"] = _now_text() if status == "done" else ""
            existing["updated_at"] = _now_text()
            rows[index] = existing
            self._write(rows)
            return _normalise(existing)
        raise KeyError("Task not found.")

    def link_schedule(
        self,
        task_id: str,
        *,
        event_id: str,
        scheduled_date: str,
        scheduled_start: str,
    ) -> dict[str, str]:
        rows = self._read()
        for index, existing in enumerate(rows):
            if existing["id"] != task_id:
                continue
            existing["schedule_event_id"] = str(event_id or "")
            existing["scheduled_date"] = _validate_date(
                scheduled_date, "Scheduled date"
            )
            existing["scheduled_start"] = _validate_time(scheduled_start)
            existing["updated_at"] = _now_text()
            rows[index] = existing
            self._write(rows)
            return _normalise(existing)
        raise KeyError("Task not found.")

    def delete(self, task_id: str) -> None:
        rows = self._read()
        kept = [row for row in rows if row["id"] != task_id]
        if len(kept) == len(rows):
            raise KeyError("Task not found.")
        self._write(kept)

    def _validated(self, values: dict[str, Any]) -> dict[str, str]:
        title = str(values.get("title", "") or "").strip()
        if not title:
            raise TodoValidationError("Task title is required.")
        if len(title) > 180:
            raise TodoValidationError("Task title is too long.")

        priority = str(values.get("priority", "Normal") or "Normal").title()
        if priority not in PRIORITIES:
            raise TodoValidationError("Choose a valid priority.")

        try:
            estimate = int(str(values.get("estimate_minutes", 25)).strip())
        except ValueError as exc:
            raise TodoValidationError("Estimate must be a whole number.") from exc
        if not 5 <= estimate <= 1440:
            raise TodoValidationError("Estimate must be between 5 and 1440 minutes.")

        scheduled_date = _validate_date(
            values.get("scheduled_date", ""), "Scheduled date"
        )
        scheduled_start = _validate_time(values.get("scheduled_start", ""))
        if scheduled_start and not scheduled_date:
            raise TodoValidationError(
                "Choose a scheduled date when a start time is entered."
            )

        return _normalise(
            {
                "title": title,
                "category": str(values.get("category", "General") or "General"),
                "notes": str(values.get("notes", "") or "")[:2000],
                "priority": priority,
                "estimate_minutes": str(estimate),
                "due_date": _validate_date(values.get("due_date", ""), "Due date"),
                "scheduled_date": scheduled_date,
                "scheduled_start": scheduled_start,
                "schedule_event_id": str(values.get("schedule_event_id", "") or ""),
            }
        )
