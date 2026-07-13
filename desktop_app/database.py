from __future__ import annotations

import csv
import hashlib
import shutil
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


PRODUCTIVE_MODES = ("Focus", "Flow", "Productive")
LOGGABLE_MODES = PRODUCTIVE_MODES + ("Personal",)

SESSION_EXPORT_COLUMNS = [
    "task",
    "category",
    "mode",
    "counts_toward_focus",
    "started_at",
    "ended_at",
    "local_date",
    "planned_minutes",
    "minutes",
    "notes",
    "completed",
    "source",
    "synced",
]


class SessionStore:
    def __init__(self, path: Path, timezone_name: str, readable_dir: Path):
        self.path = path
        self.zone = ZoneInfo(timezone_name)
        self.readable_dir = readable_dir
        self.readable_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=20)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task TEXT NOT NULL,
                    category TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'Focus',
                    counts_toward_focus INTEGER NOT NULL DEFAULT 1,
                    started_at_utc TEXT NOT NULL,
                    ended_at_utc TEXT NOT NULL,
                    local_date TEXT NOT NULL,
                    planned_minutes INTEGER NOT NULL DEFAULT 0,
                    minutes INTEGER NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    completed INTEGER NOT NULL DEFAULT 1,
                    source TEXT NOT NULL DEFAULT 'app',
                    synced INTEGER NOT NULL DEFAULT 0,
                    fingerprint TEXT UNIQUE,
                    created_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_local_date
                    ON sessions(local_date);
                CREATE INDEX IF NOT EXISTS idx_sessions_category
                    ON sessions(category);
                CREATE INDEX IF NOT EXISTS idx_sessions_synced
                    ON sessions(synced);
                CREATE TABLE IF NOT EXISTS sync_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    dates_attempted INTEGER NOT NULL DEFAULT 0,
                    dates_synced INTEGER NOT NULL DEFAULT 0,
                    http_status INTEGER,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT ''
                );
                """
            )
            self._migrate_legacy(db)
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_fingerprint "
                "ON sessions(fingerprint)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_focus_flag "
                "ON sessions(counts_toward_focus)"
            )
        self.export_readable_copy()

    @staticmethod
    def _ensure_column(
        db: sqlite3.Connection, table: str, name: str, definition: str
    ) -> bool:
        columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if name not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            return True
        return False

    def _migrate_legacy(self, db: sqlite3.Connection) -> None:
        additions = {
            "mode": "TEXT NOT NULL DEFAULT 'Focus'",
            "counts_toward_focus": "INTEGER NOT NULL DEFAULT 1",
            "planned_minutes": "INTEGER NOT NULL DEFAULT 0",
            "notes": "TEXT NOT NULL DEFAULT ''",
            "completed": "INTEGER NOT NULL DEFAULT 1",
            "source": "TEXT NOT NULL DEFAULT 'app'",
            "fingerprint": "TEXT",
            "created_at": "TEXT NOT NULL DEFAULT ''",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
        }
        added_focus_flag = False
        for name, definition in additions.items():
            added = self._ensure_column(db, "sessions", name, definition)
            if name == "counts_toward_focus" and added:
                added_focus_flag = True

        # Existing Focus/Flow records were historically sent to Pixela. Breaks were not.
        if added_focus_flag:
            db.execute(
                """
                UPDATE sessions
                SET counts_toward_focus = CASE
                    WHEN mode IN ('Focus', 'Flow') THEN 1 ELSE 0 END
                """
            )
            db.execute(
                "UPDATE sessions SET synced=1 WHERE counts_toward_focus=0"
            )

        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "UPDATE sessions SET planned_minutes=minutes WHERE planned_minutes=0"
        )
        db.execute(
            "UPDATE sessions SET created_at=? WHERE created_at='' OR created_at IS NULL",
            (now,),
        )
        db.execute(
            "UPDATE sessions SET updated_at=? WHERE updated_at='' OR updated_at IS NULL",
            (now,),
        )

    @staticmethod
    def _fingerprint(values: dict[str, Any]) -> str:
        raw = "|".join(
            str(values.get(key, ""))
            for key in (
                "task",
                "category",
                "mode",
                "counts_toward_focus",
                "started_at_utc",
                "ended_at_utc",
                "minutes",
                "source",
            )
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def add_session(
        self,
        *,
        task: str,
        category: str,
        mode: str,
        counts_toward_focus: bool,
        started_at: datetime,
        ended_at: datetime,
        planned_minutes: int,
        minutes: int,
        notes: str = "",
        completed: bool = True,
        source: str = "app",
    ) -> int:
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=self.zone)
        if ended_at.tzinfo is None:
            ended_at = ended_at.replace(tzinfo=self.zone)

        local_date = ended_at.astimezone(self.zone).strftime("%Y%m%d")
        now = datetime.now(timezone.utc).isoformat()
        focus_flag = 1 if counts_toward_focus else 0
        values = {
            "task": task.strip() or "Untitled session",
            "category": category.strip() or "General",
            "mode": mode.strip() or "Focus",
            "counts_toward_focus": focus_flag,
            "started_at_utc": started_at.astimezone(timezone.utc).isoformat(),
            "ended_at_utc": ended_at.astimezone(timezone.utc).isoformat(),
            "local_date": local_date,
            "planned_minutes": max(0, int(planned_minutes)),
            "minutes": max(1, int(minutes)),
            "notes": notes.strip(),
            "completed": 1 if completed else 0,
            "source": source,
            # Non-focus records are deliberately not sent to Pixela and are complete locally.
            "synced": 0 if focus_flag else 1,
            "created_at": now,
            "updated_at": now,
        }
        values["fingerprint"] = self._fingerprint(values)

        with self.connect() as db:
            cursor = db.execute(
                """
                INSERT INTO sessions(
                    task, category, mode, counts_toward_focus,
                    started_at_utc, ended_at_utc, local_date,
                    planned_minutes, minutes, notes, completed,
                    source, synced, fingerprint, created_at, updated_at
                ) VALUES(
                    :task, :category, :mode, :counts_toward_focus,
                    :started_at_utc, :ended_at_utc, :local_date,
                    :planned_minutes, :minutes, :notes, :completed,
                    :source, :synced, :fingerprint, :created_at, :updated_at
                )
                """,
                values,
            )
            session_id = int(cursor.lastrowid)
        self.export_readable_copy()
        return session_id

    def import_sessions(self, rows: list[dict[str, Any]]) -> tuple[int, int]:
        inserted = 0
        skipped = 0
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            for item in rows:
                values = dict(item)
                mode = str(values.get("mode", "Focus"))
                values.setdefault(
                    "counts_toward_focus", 1 if mode in {"Focus", "Flow"} else 0
                )
                values["counts_toward_focus"] = (
                    1 if int(values["counts_toward_focus"]) else 0
                )
                values.setdefault("created_at", now)
                values.setdefault("updated_at", now)
                values.setdefault(
                    "synced", 0 if values["counts_toward_focus"] else 1
                )
                values.setdefault("source", "csv-import")
                values["fingerprint"] = self._fingerprint(values)
                try:
                    db.execute(
                        """
                        INSERT INTO sessions(
                            task, category, mode, counts_toward_focus,
                            started_at_utc, ended_at_utc, local_date,
                            planned_minutes, minutes, notes, completed,
                            source, synced, fingerprint, created_at, updated_at
                        ) VALUES(
                            :task, :category, :mode, :counts_toward_focus,
                            :started_at_utc, :ended_at_utc, :local_date,
                            :planned_minutes, :minutes, :notes, :completed,
                            :source, :synced, :fingerprint, :created_at, :updated_at
                        )
                        """,
                        values,
                    )
                    inserted += 1
                except sqlite3.IntegrityError:
                    skipped += 1
        if inserted:
            self.export_readable_copy()
        return inserted, skipped

    def _session_filter_sql(
        self,
        *,
        search: str = "",
        category: str = "All",
        days: int | None = None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if search.strip():
            clauses.append("(task LIKE ? OR notes LIKE ? OR category LIKE ? OR mode LIKE ?)")
            pattern = f"%{search.strip()}%"
            params.extend([pattern, pattern, pattern, pattern])
        if category and category != "All":
            clauses.append("category=?")
            params.append(category)
        if days:
            cutoff = (datetime.now(self.zone) - timedelta(days=days - 1)).strftime(
                "%Y%m%d"
            )
            clauses.append("local_date>=?")
            params.append(cutoff)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return where, params

    def count_sessions(
        self,
        *,
        search: str = "",
        category: str = "All",
        days: int | None = None,
    ) -> int:
        where, params = self._session_filter_sql(
            search=search,
            category=category,
            days=days,
        )
        with self.connect() as db:
            row = db.execute(
                f"SELECT COUNT(*) AS total FROM sessions{where}",
                params,
            ).fetchone()
        return int(row["total"] if row is not None else 0)

    def list_sessions(
        self,
        *,
        search: str = "",
        category: str = "All",
        days: int | None = None,
        limit: int = 5000,
        offset: int = 0,
    ) -> list[sqlite3.Row]:
        where, params = self._session_filter_sql(
            search=search,
            category=category,
            days=days,
        )
        params.extend([max(1, int(limit)), max(0, int(offset))])
        with self.connect() as db:
            return db.execute(
                f"""
                SELECT * FROM sessions{where}
                ORDER BY started_at_utc DESC, id DESC LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()

    def get_session(self, session_id: int) -> sqlite3.Row | None:
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM sessions WHERE id=?", (int(session_id),)
            ).fetchone()

    def update_session(
        self,
        session_id: int,
        *,
        task: str,
        category: str,
        mode: str,
        counts_toward_focus: bool,
        notes: str,
        minutes: int,
    ) -> None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM sessions WHERE id=?", (int(session_id),)
            ).fetchone()
            if row is None:
                return
            values = dict(row)
            focus_flag = 1 if counts_toward_focus else 0
            values.update(
                {
                    "task": task.strip() or "Untitled session",
                    "category": category.strip() or "General",
                    "mode": mode.strip() or "Focus",
                    "counts_toward_focus": focus_flag,
                    "notes": notes.strip(),
                    "minutes": max(1, int(minutes)),
                    "synced": 0 if focus_flag else 1,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            values["fingerprint"] = self._fingerprint(values)
            db.execute(
                """
                UPDATE sessions SET task=:task, category=:category,
                    mode=:mode, counts_toward_focus=:counts_toward_focus,
                    notes=:notes, minutes=:minutes, synced=:synced,
                    updated_at=:updated_at, fingerprint=:fingerprint
                WHERE id=:id
                """,
                values,
            )
            # Any focus-counted total on this date may now have changed.
            db.execute(
                "UPDATE sessions SET synced=0 "
                "WHERE local_date=? AND counts_toward_focus=1",
                (row["local_date"],),
            )
        self.export_readable_copy()

    def delete_sessions(self, ids: Iterable[int]) -> int:
        ids = [int(value) for value in ids]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self.connect() as db:
            dates = [
                row[0]
                for row in db.execute(
                    f"SELECT DISTINCT local_date FROM sessions "
                    f"WHERE id IN ({placeholders}) AND counts_toward_focus=1",
                    ids,
                ).fetchall()
            ]
            before = db.total_changes
            db.execute(f"DELETE FROM sessions WHERE id IN ({placeholders})", ids)
            changed = db.total_changes - before
            for date in dates:
                db.execute(
                    "UPDATE sessions SET synced=0 "
                    "WHERE local_date=? AND counts_toward_focus=1",
                    (date,),
                )
        if changed:
            self.export_readable_copy()
        return changed

    def distinct_categories(self) -> list[str]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT DISTINCT category FROM sessions WHERE category<>'' ORDER BY category"
            ).fetchall()
        return [str(row[0]) for row in rows]

    def distinct_tasks(self, limit: int = 100) -> list[str]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT task, MAX(id) AS newest FROM sessions
                WHERE task<>'' GROUP BY task ORDER BY newest DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def total_for_date(self, local_date: str) -> int:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT COALESCE(SUM(minutes), 0) FROM sessions
                WHERE local_date=? AND completed=1 AND counts_toward_focus=1
                """,
                (local_date,),
            ).fetchone()
        return int(row[0])

    def unsynced_dates(self) -> list[str]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT DISTINCT local_date FROM sessions
                WHERE synced=0 AND completed=1 AND counts_toward_focus=1
                ORDER BY local_date
                """
            ).fetchall()
        return [str(row[0]) for row in rows]

    def mark_date_synced(self, local_date: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE sessions SET synced=1 "
                "WHERE local_date=? AND counts_toward_focus=1",
                (local_date,),
            )

    def mark_all_unsynced(self) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE sessions SET synced=0 WHERE counts_toward_focus=1"
            )
            db.execute(
                "UPDATE sessions SET synced=1 WHERE counts_toward_focus=0"
            )

    def today_summary(self) -> dict[str, int]:
        today = datetime.now(self.zone).strftime("%Y%m%d")
        with self.connect() as db:
            row = db.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN counts_toward_focus=1 THEN minutes ELSE 0 END), 0)
                        AS focus_minutes,
                    SUM(CASE WHEN counts_toward_focus=1 THEN 1 ELSE 0 END)
                        AS focus_sessions,
                    COALESCE(SUM(CASE
                        WHEN mode IN ('Focus','Flow','Productive')
                             AND counts_toward_focus=0 THEN minutes ELSE 0 END), 0)
                        AS other_productive_minutes,
                    SUM(CASE
                        WHEN mode IN ('Focus','Flow','Productive')
                             AND counts_toward_focus=0 THEN 1 ELSE 0 END)
                        AS other_productive_sessions,
                    COALESCE(SUM(CASE WHEN mode='Personal' THEN minutes ELSE 0 END), 0)
                        AS personal_minutes
                FROM sessions WHERE local_date=? AND completed=1
                """,
                (today,),
            ).fetchone()
        focus_minutes = int(row["focus_minutes"] or 0)
        other_minutes = int(row["other_productive_minutes"] or 0)
        focus_sessions = int(row["focus_sessions"] or 0)
        other_sessions = int(row["other_productive_sessions"] or 0)
        return {
            "minutes": focus_minutes,
            "sessions": focus_sessions,
            "focus_minutes": focus_minutes,
            "focus_sessions": focus_sessions,
            "other_productive_minutes": other_minutes,
            "other_productive_sessions": other_sessions,
            "total_productive_minutes": focus_minutes + other_minutes,
            "total_productive_sessions": focus_sessions + other_sessions,
            "personal_minutes": int(row["personal_minutes"] or 0),
        }

    def _date_clause(self, days: int | None) -> tuple[str, list[Any]]:
        if not days:
            return "", []
        cutoff = (datetime.now(self.zone) - timedelta(days=days - 1)).strftime(
            "%Y%m%d"
        )
        return " AND local_date>=?", [cutoff]

    @staticmethod
    def _scope_clause(scope: str) -> str:
        scopes = {
            "focus": "counts_toward_focus=1",
            "other_productive": (
                "mode IN ('Focus','Flow','Productive') AND counts_toward_focus=0"
            ),
            "productive": "mode IN ('Focus','Flow','Productive')",
            "personal": "mode='Personal'",
            "all": "mode IN ('Focus','Flow','Productive','Personal')",
        }
        return scopes.get(scope, scopes["focus"])

    def analytics_summary(self, days: int | None) -> dict[str, float | int]:
        date_clause, params = self._date_clause(days)
        with self.connect() as db:
            row = db.execute(
                f"""
                SELECT
                    COALESCE(SUM(CASE WHEN counts_toward_focus=1 THEN minutes ELSE 0 END), 0)
                        AS focus_minutes,
                    SUM(CASE WHEN counts_toward_focus=1 THEN 1 ELSE 0 END)
                        AS focus_sessions,
                    COUNT(DISTINCT CASE WHEN counts_toward_focus=1 THEN local_date END)
                        AS focus_active_days,
                    COALESCE(SUM(CASE
                        WHEN mode IN ('Focus','Flow','Productive')
                             AND counts_toward_focus=0 THEN minutes ELSE 0 END), 0)
                        AS other_productive_minutes,
                    SUM(CASE
                        WHEN mode IN ('Focus','Flow','Productive')
                             AND counts_toward_focus=0 THEN 1 ELSE 0 END)
                        AS other_productive_sessions,
                    COALESCE(SUM(CASE
                        WHEN mode IN ('Focus','Flow','Productive') THEN minutes ELSE 0 END), 0)
                        AS total_productive_minutes,
                    SUM(CASE
                        WHEN mode IN ('Focus','Flow','Productive') THEN 1 ELSE 0 END)
                        AS total_productive_sessions,
                    COALESCE(SUM(CASE WHEN mode='Personal' THEN minutes ELSE 0 END), 0)
                        AS personal_minutes
                FROM sessions WHERE completed=1 {date_clause}
                """,
                params,
            ).fetchone()
        focus_minutes = int(row["focus_minutes"] or 0)
        active_days = int(row["focus_active_days"] or 0)
        return {
            "minutes": focus_minutes,
            "sessions": int(row["focus_sessions"] or 0),
            "focus_minutes": focus_minutes,
            "focus_sessions": int(row["focus_sessions"] or 0),
            "other_productive_minutes": int(row["other_productive_minutes"] or 0),
            "other_productive_sessions": int(row["other_productive_sessions"] or 0),
            "total_productive_minutes": int(row["total_productive_minutes"] or 0),
            "total_productive_sessions": int(row["total_productive_sessions"] or 0),
            "personal_minutes": int(row["personal_minutes"] or 0),
            "active_days": active_days,
            "daily_average": round(focus_minutes / active_days, 1) if active_days else 0.0,
            "streak": self.current_streak(),
        }

    def summary_between(
        self,
        start_date: datetime.date,
        end_date: datetime.date,
    ) -> dict[str, float | int]:
        """Return exact calendar-period totals, inclusive."""
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        start_key = start_date.strftime("%Y%m%d")
        end_key = end_date.strftime("%Y%m%d")
        with self.connect() as db:
            row = db.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN counts_toward_focus=1 THEN minutes ELSE 0 END), 0)
                        AS focus_minutes,
                    SUM(CASE WHEN counts_toward_focus=1 THEN 1 ELSE 0 END)
                        AS focus_sessions,
                    COUNT(DISTINCT CASE WHEN counts_toward_focus=1 THEN local_date END)
                        AS active_days,
                    COALESCE(SUM(CASE
                        WHEN mode IN ('Focus','Flow','Productive')
                             AND counts_toward_focus=0 THEN minutes ELSE 0 END), 0)
                        AS other_productive_minutes,
                    SUM(CASE
                        WHEN mode IN ('Focus','Flow','Productive')
                             AND counts_toward_focus=0 THEN 1 ELSE 0 END)
                        AS other_productive_sessions,
                    COALESCE(SUM(CASE
                        WHEN mode IN ('Focus','Flow','Productive') THEN minutes ELSE 0 END), 0)
                        AS total_productive_minutes,
                    SUM(CASE
                        WHEN mode IN ('Focus','Flow','Productive') THEN 1 ELSE 0 END)
                        AS total_productive_sessions,
                    COALESCE(SUM(CASE WHEN mode='Personal' THEN minutes ELSE 0 END), 0)
                        AS personal_minutes
                FROM sessions
                WHERE completed=1 AND local_date BETWEEN ? AND ?
                """,
                (start_key, end_key),
            ).fetchone()
        focus_minutes = int(row["focus_minutes"] or 0)
        active_days = int(row["active_days"] or 0)
        return {
            "focus_minutes": focus_minutes,
            "focus_sessions": int(row["focus_sessions"] or 0),
            "other_productive_minutes": int(row["other_productive_minutes"] or 0),
            "other_productive_sessions": int(row["other_productive_sessions"] or 0),
            "total_productive_minutes": int(row["total_productive_minutes"] or 0),
            "total_productive_sessions": int(row["total_productive_sessions"] or 0),
            "personal_minutes": int(row["personal_minutes"] or 0),
            "active_days": active_days,
            "daily_average": round(focus_minutes / active_days, 1) if active_days else 0.0,
        }
    def current_streak(self) -> int:
        with self.connect() as db:
            dates = {
                str(row[0])
                for row in db.execute(
                    """
                    SELECT DISTINCT local_date FROM sessions
                    WHERE completed=1 AND counts_toward_focus=1 AND minutes>0
                    """
                ).fetchall()
            }
        current = datetime.now(self.zone).date()
        if current.strftime("%Y%m%d") not in dates:
            current -= timedelta(days=1)
        streak = 0
        while current.strftime("%Y%m%d") in dates:
            streak += 1
            current -= timedelta(days=1)
        return streak

    def daily_totals(
        self, days: int | None, scope: str = "focus"
    ) -> list[tuple[str, int]]:
        date_clause, params = self._date_clause(days)
        scope_clause = self._scope_clause(scope)
        with self.connect() as db:
            rows = db.execute(
                f"""
                SELECT local_date, SUM(minutes) AS total FROM sessions
                WHERE completed=1 AND {scope_clause} {date_clause}
                GROUP BY local_date ORDER BY local_date
                """,
                params,
            ).fetchall()
        return [(str(row[0]), int(row[1])) for row in rows]

    def category_totals(
        self, days: int | None, scope: str = "productive"
    ) -> list[tuple[str, int]]:
        date_clause, params = self._date_clause(days)
        scope_clause = self._scope_clause(scope)
        with self.connect() as db:
            rows = db.execute(
                f"""
                SELECT category, SUM(minutes) AS total FROM sessions
                WHERE completed=1 AND {scope_clause} {date_clause}
                GROUP BY category ORDER BY total DESC
                """,
                params,
            ).fetchall()
        return [(str(row[0]), int(row[1])) for row in rows]

    def weekday_totals(
        self, days: int | None, scope: str = "focus"
    ) -> list[tuple[int, int]]:
        rows = self.daily_totals(days, scope)
        totals = {index: 0 for index in range(7)}
        for date_text, minutes in rows:
            day = datetime.strptime(date_text, "%Y%m%d").weekday()
            totals[day] += minutes
        return list(totals.items())

    def add_sync_run(self, values: dict[str, Any]) -> int:
        with self.connect() as db:
            cursor = db.execute(
                """
                INSERT INTO sync_runs(
                    started_at, finished_at, status, dates_attempted,
                    dates_synced, http_status, duration_ms, message
                ) VALUES(
                    :started_at, :finished_at, :status, :dates_attempted,
                    :dates_synced, :http_status, :duration_ms, :message
                )
                """,
                values,
            )
            db.execute(
                """
                DELETE FROM sync_runs WHERE id NOT IN(
                    SELECT id FROM sync_runs ORDER BY id DESC LIMIT 500
                )
                """
            )
            return int(cursor.lastrowid)

    def sync_runs(self, limit: int = 100) -> list[sqlite3.Row]:
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()

    def latest_sync_run(self) -> sqlite3.Row | None:
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()

    def export_csv(self, path: Path, rows: list[sqlite3.Row] | None = None) -> int:
        if rows is None:
            rows = self.list_sessions(limit=1_000_000)
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=SESSION_EXPORT_COLUMNS)
            writer.writeheader()
            for row in reversed(rows):
                started = datetime.fromisoformat(row["started_at_utc"]).astimezone(
                    self.zone
                )
                ended = datetime.fromisoformat(row["ended_at_utc"]).astimezone(self.zone)
                writer.writerow(
                    {
                        "task": row["task"],
                        "category": row["category"],
                        "mode": row["mode"],
                        "counts_toward_focus": row["counts_toward_focus"],
                        "started_at": started.isoformat(timespec="seconds"),
                        "ended_at": ended.isoformat(timespec="seconds"),
                        "local_date": row["local_date"],
                        "planned_minutes": row["planned_minutes"],
                        "minutes": row["minutes"],
                        "notes": row["notes"],
                        "completed": row["completed"],
                        "source": row["source"],
                        "synced": row["synced"],
                    }
                )
        return len(rows)

    def export_readable_copy(self) -> None:
        try:
            self.export_csv(self.readable_dir / "focus_sessions.csv")
            sync_rows = self.sync_runs(limit=1000)
            with (self.readable_dir / "sync_log.csv").open(
                "w", newline="", encoding="utf-8-sig"
            ) as handle:
                columns = [
                    "started_at",
                    "finished_at",
                    "status",
                    "dates_attempted",
                    "dates_synced",
                    "http_status",
                    "duration_ms",
                    "message",
                ]
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                for row in reversed(sync_rows):
                    writer.writerow({key: row[key] for key in columns})
        except (OSError, sqlite3.Error):
            pass

    def backup_database(self, destination: Path) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.path, destination)
