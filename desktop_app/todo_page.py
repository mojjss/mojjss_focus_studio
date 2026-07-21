from __future__ import annotations

from datetime import date, datetime, timedelta
from tkinter import messagebox, ttk
from typing import Any

import customtkinter as ctk

from todo_store import PRIORITIES, TodoStore, TodoValidationError


class TodoPage(ctk.CTkFrame):
    """Local task list that can time-box a task and load it into TimerPage."""

    FILTERS = ("Inbox", "Today", "Upcoming", "Done")

    def __init__(self, master, app, timer_page, store: TodoStore):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.timer_page = timer_page
        self.store = store
        self.filter_var = ctk.StringVar(value="Inbox")
        self.quick_title_var = ctk.StringVar()
        self.status_var = ctk.StringVar(value="Select a task to edit, schedule, or focus.")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=24, pady=(20, 8), sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="To Do",
            font=ctk.CTkFont(size=28, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text="Capture a task, time-box it, then send it to the timer.",
            text_color=("#64748b", "#94a3b8"),
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        quick = ctk.CTkFrame(self, corner_radius=14)
        quick.grid(row=1, column=0, padx=24, pady=(0, 10), sticky="ew")
        quick.grid_columnconfigure(0, weight=1)
        entry = ctk.CTkEntry(
            quick,
            textvariable=self.quick_title_var,
            placeholder_text="Quick add a task…",
            height=42,
        )
        entry.grid(row=0, column=0, padx=(14, 8), pady=14, sticky="ew")
        entry.bind("<Return>", lambda _event: self.quick_add())
        ctk.CTkButton(
            quick,
            text="Add",
            width=90,
            height=42,
            command=self.quick_add,
        ).grid(row=0, column=1, padx=(0, 14), pady=14)

        filters = ctk.CTkFrame(self, fg_color="transparent")
        filters.grid(row=2, column=0, padx=24, pady=(0, 8), sticky="ew")
        for column, name in enumerate(self.FILTERS):
            ctk.CTkRadioButton(
                filters,
                text=name,
                variable=self.filter_var,
                value=name,
                command=self.refresh,
            ).grid(row=0, column=column, padx=(0, 18), sticky="w")

        table_card = ctk.CTkFrame(self, corner_radius=14)
        table_card.grid(row=3, column=0, padx=24, pady=(0, 10), sticky="nsew")
        table_card.grid_columnconfigure(0, weight=1)
        table_card.grid_rowconfigure(0, weight=1)

        columns = ("task", "category", "priority", "estimate", "due", "scheduled")
        self.tree = ttk.Treeview(
            table_card,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        headings = {
            "task": "Task",
            "category": "Category",
            "priority": "Priority",
            "estimate": "Estimate",
            "due": "Due",
            "scheduled": "Scheduled",
        }
        widths = {
            "task": 330,
            "category": 120,
            "priority": 90,
            "estimate": 85,
            "due": 105,
            "scheduled": 150,
        }
        for name in columns:
            self.tree.heading(name, text=headings[name])
            self.tree.column(
                name,
                width=widths[name],
                minwidth=70,
                stretch=name in {"task", "scheduled"},
            )
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(12, 0), pady=12)
        scrollbar = ttk.Scrollbar(table_card, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 12), pady=12)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<Double-1>", lambda _event: self.edit_selected())

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=4, column=0, padx=24, pady=(0, 6), sticky="ew")
        for column in range(6):
            actions.grid_columnconfigure(column, weight=1)

        buttons = (
            ("New", self.add_dialog),
            ("Edit", self.edit_selected),
            ("Done / Reopen", self.toggle_done),
            ("Delete", self.delete_selected),
            ("Add to Schedule", self.schedule_selected),
            ("Use in Timer", self.use_selected),
        )
        for column, (label, command) in enumerate(buttons):
            options: dict[str, Any] = {}
            if label == "Use in Timer":
                options.update(
                    fg_color=("#0f766e", "#0f766e"),
                    hover_color=("#115e59", "#115e59"),
                )
            ctk.CTkButton(
                actions,
                text=label,
                height=38,
                command=command,
                **options,
            ).grid(
                row=0,
                column=column,
                padx=(0 if column == 0 else 4, 0 if column == 5 else 4),
                sticky="ew",
            )

        ctk.CTkLabel(
            self,
            textvariable=self.status_var,
            anchor="w",
            text_color=("#64748b", "#94a3b8"),
        ).grid(row=5, column=0, padx=28, pady=(0, 16), sticky="ew")

        self.refresh()

    def refresh(self) -> None:
        selected = self.selected_id()
        for item in self.tree.get_children():
            self.tree.delete(item)

        today = date.today()
        current_filter = self.filter_var.get()
        rows = self.store.list()
        visible: list[dict[str, str]] = []
        for row in rows:
            if current_filter == "Done":
                if row["status"] != "done":
                    continue
            else:
                if row["status"] == "done":
                    continue
                chosen_date = row["scheduled_date"] or row["due_date"]
                if current_filter == "Today" and chosen_date != today.isoformat():
                    continue
                if current_filter == "Upcoming":
                    if not chosen_date or chosen_date <= today.isoformat():
                        continue
            visible.append(row)

        for row in visible:
            scheduled = row["scheduled_date"]
            if scheduled and row["scheduled_start"]:
                scheduled += " " + row["scheduled_start"]
            self.tree.insert(
                "",
                "end",
                iid=row["id"],
                values=(
                    ("✓ " if row["status"] == "done" else "") + row["title"],
                    row["category"],
                    row["priority"],
                    f'{row["estimate_minutes"]} min',
                    row["due_date"],
                    scheduled,
                ),
            )
        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)
            self.tree.see(selected)
        self.status_var.set(f"{len(visible)} task(s) in {current_filter}.")

    def selected_id(self) -> str:
        selection = self.tree.selection()
        return str(selection[0]) if selection else ""

    def selected_task(self) -> dict[str, str] | None:
        task_id = self.selected_id()
        if not task_id:
            messagebox.showinfo("To Do", "Select a task first.")
            return None
        task = self.store.get(task_id)
        if task is None:
            self.refresh()
            messagebox.showerror("To Do", "The selected task no longer exists.")
        return task

    def quick_add(self) -> None:
        title = self.quick_title_var.get().strip()
        if not title:
            return
        try:
            task = self.store.add(
                {
                    "title": title,
                    "category": "General",
                    "priority": "Normal",
                    "estimate_minutes": 25,
                }
            )
        except TodoValidationError as exc:
            messagebox.showerror("Could not add task", str(exc))
            return
        self.quick_title_var.set("")
        self.filter_var.set("Inbox")
        self.refresh()
        if self.tree.exists(task["id"]):
            self.tree.selection_set(task["id"])

    def add_dialog(self) -> None:
        values = self._task_dialog(None)
        if values is None:
            return
        try:
            task = self.store.add(values)
        except TodoValidationError as exc:
            messagebox.showerror("Could not add task", str(exc))
            return
        self.filter_var.set("Inbox")
        self.refresh()
        if self.tree.exists(task["id"]):
            self.tree.selection_set(task["id"])

    def edit_selected(self) -> None:
        task = self.selected_task()
        if task is None:
            return
        values = self._task_dialog(task)
        if values is None:
            return
        try:
            self.store.update(task["id"], values)
        except TodoValidationError as exc:
            messagebox.showerror("Could not save task", str(exc))
            return
        self.refresh()

    def toggle_done(self) -> None:
        task = self.selected_task()
        if task is None:
            return
        if task["status"] == "done":
            self.store.reopen(task["id"])
        else:
            self.store.complete(task["id"])
        self.refresh()

    def delete_selected(self) -> None:
        task = self.selected_task()
        if task is None:
            return
        if not messagebox.askyesno("Delete task", f'Delete “{task["title"]}”?'):
            return
        self.store.delete(task["id"])
        self.refresh()

    def schedule_selected(self) -> None:
        task = self.selected_task()
        if task is None:
            return
        try:
            event = self._schedule_task(task)
        except Exception as exc:
            messagebox.showerror("Could not schedule task", str(exc))
            return
        self.status_var.set(
            f'Scheduled “{task["title"]}” for {event["date"]} at {event["start"]}.'
        )
        self.refresh()

    def use_selected(self) -> None:
        task = self.selected_task()
        if task is None:
            return
        if task["status"] == "done":
            if not messagebox.askyesno(
                "Completed task",
                "This task is already completed. Load it into the timer anyway?",
            ):
                return
        try:
            event = self._schedule_task(task)
            estimate = int(task["estimate_minutes"])
            self.timer_page.task_var.set(task["title"])
            self.timer_page.category_var.set(task["category"] or "General")
            self.timer_page.change_mode("Focus")
            self.timer_page.set_duration(estimate)
            self.timer_page.timer_status_var.set(
                f'Loaded from To Do · scheduled {event["date"]} {event["start"]}'
            )
            self.app.refresh_data_views()
            self.app.publish_cloud_snapshot_now()
            self.app.show_page("Timer")
        except Exception as exc:
            messagebox.showerror("Could not use task", str(exc))

    def _schedule_task(self, task: dict[str, str]) -> dict[str, str]:
        now = datetime.now(self.app.zone)
        scheduled_date = task["scheduled_date"] or date.today().isoformat()
        scheduled_start = task["scheduled_start"]
        if not scheduled_start:
            rounded = now.replace(second=0, microsecond=0)
            extra = (5 - rounded.minute % 5) % 5
            if extra == 0 and rounded <= now:
                extra = 5
            rounded += timedelta(minutes=extra)
            if rounded.date().isoformat() != scheduled_date:
                rounded = datetime.fromisoformat(
                    scheduled_date + "T09:00:00"
                ).replace(tzinfo=self.app.zone)
            scheduled_start = rounded.strftime("%H:%M")

        start_dt = datetime.fromisoformat(
            f"{scheduled_date}T{scheduled_start}:00"
        )
        estimate = max(5, min(1440, int(task["estimate_minutes"])))
        end_dt = start_dt + timedelta(minutes=estimate)
        if end_dt.date() != start_dt.date():
            end_dt = start_dt.replace(hour=23, minute=59)
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(minutes=1)

        values = {
            "date": scheduled_date,
            "start": scheduled_start,
            "end": end_dt.strftime("%H:%M"),
            "title": task["title"],
            "category": task["category"] or "General",
            "notes": task["notes"],
        }
        event = None
        linked_id = task["schedule_event_id"]
        if linked_id:
            try:
                event = self.app.schedule_store.update(linked_id, values)
            except Exception:
                event = None
        if event is None:
            event = self.app.schedule_store.add(values)

        self.store.link_schedule(
            task["id"],
            event_id=event["id"],
            scheduled_date=scheduled_date,
            scheduled_start=scheduled_start,
        )
        self.app.refresh_data_views()
        self.app.publish_cloud_snapshot_now()
        return event

    def _task_dialog(
        self, task: dict[str, str] | None
    ) -> dict[str, str] | None:
        current = task or {}
        result: dict[str, str] | None = None

        window = ctk.CTkToplevel(self)
        window.title("Edit task" if task else "New task")
        window.geometry("620x650")
        window.minsize(560, 580)
        window.transient(self.winfo_toplevel())
        window.grab_set()
        window.grid_columnconfigure(1, weight=1)
        window.grid_rowconfigure(9, weight=1)

        variables = {
            "title": ctk.StringVar(value=current.get("title", "")),
            "category": ctk.StringVar(value=current.get("category", "General")),
            "priority": ctk.StringVar(value=current.get("priority", "Normal")),
            "estimate_minutes": ctk.StringVar(
                value=current.get("estimate_minutes", "25")
            ),
            "due_date": ctk.StringVar(value=current.get("due_date", "")),
            "scheduled_date": ctk.StringVar(
                value=current.get("scheduled_date", "")
            ),
            "scheduled_start": ctk.StringVar(
                value=current.get("scheduled_start", "")
            ),
        }

        ctk.CTkLabel(
            window,
            text="Task details",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, padx=22, pady=(20, 12), sticky="w")

        def entry_row(row: int, label: str, key: str, placeholder: str = ""):
            ctk.CTkLabel(window, text=label).grid(
                row=row, column=0, padx=(22, 10), pady=7, sticky="w"
            )
            entry = ctk.CTkEntry(
                window,
                textvariable=variables[key],
                placeholder_text=placeholder,
            )
            entry.grid(row=row, column=1, padx=(0, 22), pady=7, sticky="ew")
            return entry

        title_entry = entry_row(1, "Title", "title")
        entry_row(2, "Category", "category")
        ctk.CTkLabel(window, text="Priority").grid(
            row=3, column=0, padx=(22, 10), pady=7, sticky="w"
        )
        ctk.CTkOptionMenu(
            window,
            variable=variables["priority"],
            values=list(PRIORITIES),
        ).grid(row=3, column=1, padx=(0, 22), pady=7, sticky="ew")
        entry_row(4, "Estimate (minutes)", "estimate_minutes")
        entry_row(5, "Due date", "due_date", "YYYY-MM-DD, optional")
        entry_row(6, "Scheduled date", "scheduled_date", "YYYY-MM-DD, optional")
        entry_row(7, "Start time", "scheduled_start", "HH:MM, optional")

        ctk.CTkLabel(window, text="Notes").grid(
            row=8, column=0, padx=(22, 10), pady=7, sticky="nw"
        )
        notes = ctk.CTkTextbox(window, height=150)
        notes.grid(row=8, column=1, padx=(0, 22), pady=7, sticky="nsew")
        notes.insert("1.0", current.get("notes", ""))

        buttons = ctk.CTkFrame(window, fg_color="transparent")
        buttons.grid(row=10, column=0, columnspan=2, padx=22, pady=18, sticky="ew")
        buttons.grid_columnconfigure((0, 1), weight=1)

        def save() -> None:
            nonlocal result
            candidate = {key: variable.get() for key, variable in variables.items()}
            candidate["notes"] = notes.get("1.0", "end").strip()
            candidate["schedule_event_id"] = current.get("schedule_event_id", "")
            try:
                # Validate without writing.
                self.store._validated(candidate)
            except TodoValidationError as exc:
                messagebox.showerror("Invalid task", str(exc), parent=window)
                return
            result = candidate
            window.destroy()

        ctk.CTkButton(
            buttons,
            text="Cancel",
            fg_color=("#64748b", "#475569"),
            command=window.destroy,
        ).grid(row=0, column=0, padx=(0, 6), sticky="ew")
        ctk.CTkButton(buttons, text="Save", command=save).grid(
            row=0, column=1, padx=(6, 0), sticky="ew"
        )
        title_entry.focus_set()
        window.wait_window()
        return result
