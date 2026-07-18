from __future__ import annotations

import argparse
import getpass
import io
import json
import os
import sys
import threading
import uuid
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any
from zoneinfo import ZoneInfo

import customtkinter as ctk
from PIL import Image

from charts import AnalyticsChart
from cloud_client import CloudDashboardPublisher
from cloud_sync import CloudStateSynchronizer
from camera_security import derive_camera_password, has_camera_password
from version import APP_VERSION

from tailscale_camera import (
    TailscaleCameraError,
    TailscaleCameraServer,
    test_camera,
)
from tailscale_tools import (
    TailscaleSetupError,
    configure_serve,
    serve_status_text,
)
from config_store import DEFAULT_CONFIG, load_config, save_config
from csv_tools import CsvImportError, read_session_csv
from database import SessionStore
from pixela_client import PixelaClient, PixelaError
from schedule_store import ScheduleStore, ScheduleValidationError
from monitor_server import MonitorServer
from tunnel_monitor import TunnelHealth, TunnelHealthMonitor

try:
    import resvg_py
except ImportError:  # Optional only for the in-app official SVG preview.
    resvg_py = None


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
CONFIG_PATH = APP_DIR / "config.json"
DB_PATH = APP_DIR / "focus_history.db"
READABLE_DIR = APP_DIR / "data" / "readable"
EXPORT_DIR = APP_DIR / "exports"
TIMER_STATE_PATH = APP_DIR / "timer_state.json"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class TimerState:
    mode: str = "Focus"
    duration_seconds: int = 25 * 60
    remaining_seconds: int = 25 * 60
    elapsed_seconds: int = 0
    running: bool = False
    paused: bool = False
    started_at: datetime | None = None
    monotonic_anchor: float | None = None
    session_id: str = ""
    revision: int = 0
    updated_at: datetime | None = None
    sync_status: str = "idle"


class FocusApp(ctk.CTk):
    def __init__(self, config: dict[str, Any]):
        super().__init__()
        self.config_data = config
        self.zone = ZoneInfo(config["timezone"])
        self.store = SessionStore(DB_PATH, config["timezone"], READABLE_DIR)
        self.schedule_store = ScheduleStore(APP_DIR / "schedule.csv")
        self.pixela = self.make_pixela_client()
        self._monitor_lock = threading.Lock()
        self._monitor_snapshot: dict[str, Any] = {
            "running": False,
            "paused": False,
            "mode": "Focus",
            "task": "",
            "category": "Research",
            "display_seconds": int(config["focus_minutes"]) * 60,
            "status": "Ready",
        }
        self._monitor_server: MonitorServer | None = None
        self._monitor_graph_cache: bytes | None = None
        self._monitor_graph_cache_at = 0.0
        self.cloud_publisher: CloudDashboardPublisher | None = None
        self.cloud_syncer: CloudStateSynchronizer | None = None
        self._cloud_client_since: str | None = None
        self._cloud_server_since: str | None = None
        self.camera_server: TailscaleCameraServer | None = None
        self.tunnel_monitor = TunnelHealthMonitor(
            config_provider=lambda: self.config_data,
            callback=self._set_tunnel_health_threadsafe,
        )
        self._last_tunnel_health: TunnelHealth | None = None
        self._last_tunnel_overall = ""

        self.title(f"mojjss Focus Studio v{APP_VERSION}")
        self.geometry("1420x840")
        self.minsize(1180, 720)
        self.attributes("-topmost", bool(config.get("always_on_top", False)))
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self.pages: dict[str, ctk.CTkFrame] = {}
        self.active_page = "Timer"

        self.sync_status_var = ctk.StringVar(value="Pixela: not checked")
        self.local_status_var = ctk.StringVar(value="Local database ready")
        self.cloud_status_var = ctk.StringVar(value="Cloud dashboard: disabled")
        self.camera_status_var = ctk.StringVar(value="Private camera: disabled")
        self.tunnel_status_var = ctk.StringVar(value="Tunnel: checking…")
        self.camera_enabled_var = ctk.BooleanVar(
            value=bool(config.get("remote_camera_enabled", False))
        )
        self.pixela_status_detail = "Pixela has not been checked yet."
        self.cloud_status_detail = "Cloud dashboard is disabled."
        self.camera_status_detail = "Private camera is disabled."
        self.tunnel_status_detail = "Tunnel health has not been checked yet."

        self._build_sidebar()
        self._build_content()
        self.show_page("Timer")
        self._start_monitor_server()
        self._configure_cloud_publisher()
        self._configure_cloud_syncer()
        self._configure_remote_camera()
        self.tunnel_monitor.start()
        self.after(1200, self._cloud_dashboard_tick)
        self.after(1800, self._cloud_sync_tick)
        self.after(700, self.background_connection_check)

    def publish_monitor_snapshot(self, values: dict[str, Any]) -> None:
        with self._monitor_lock:
            self._monitor_snapshot = dict(values)

    def _monitor_payload(self) -> dict[str, Any]:
        with self._monitor_lock:
            timer = dict(self._monitor_snapshot)

        now = datetime.now(self.zone)
        today_date = now.date()
        today_key = today_date.strftime("%Y%m%d")
        week_start = today_date - timedelta(days=today_date.weekday())
        week_end = week_start + timedelta(days=6)
        month_start = today_date.replace(day=1)
        if month_start.month == 12:
            next_month = month_start.replace(
                year=month_start.year + 1,
                month=1,
            )
        else:
            next_month = month_start.replace(month=month_start.month + 1)
        month_end = next_month - timedelta(days=1)

        periods = {
            "today": self.store.summary_between(today_date, today_date),
            "week": self.store.summary_between(week_start, week_end),
            "month": self.store.summary_between(month_start, month_end),
        }

        recent: list[dict[str, Any]] = []
        for row in self.store.list_sessions(days=90, limit=300):
            try:
                local_start = datetime.fromisoformat(
                    str(row["started_at_utc"])
                ).astimezone(self.zone)
                start_text = local_start.strftime("%H:%M")
                iso_date = local_start.date().isoformat()
                weekday = local_start.strftime("%A")
                display_date = local_start.strftime("%b %d, %Y")
            except (TypeError, ValueError):
                start_text = ""
                iso_date = ""
                weekday = ""
                display_date = ""
            recent.append(
                {
                    "id": int(row["id"]),
                    "sync_id": str(row["sync_id"] or ""),
                    "started_at_utc": str(row["started_at_utc"]),
                    "date": iso_date,
                    "display_date": display_date,
                    "weekday": weekday,
                    "start": start_text,
                    "task": str(row["task"]),
                    "category": str(row["category"]),
                    "mode": str(row["mode"]),
                    "minutes": int(row["minutes"]),
                    "planned_minutes": int(row["planned_minutes"]),
                    "counts_toward_focus": bool(
                        row["counts_toward_focus"]
                    ),
                    "notes": str(row["notes"] or "")[:500],
                    "source": str(row["source"] or ""),
                }
            )
        recent.reverse()

        range_start = min(week_start, month_start)
        range_end = max(week_end, month_end)
        schedule_range = self.schedule_store.events_between(
            range_start,
            range_end,
        )

        return {
            "ok": True,
            "server_time": now.isoformat(),
            "timezone": str(self.config_data.get("timezone", "")),
            "timer": timer,
            "today": self.store.today_summary(),
            "periods": periods,
            "calendar": {
                "today": today_date.isoformat(),
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "month_start": month_start.isoformat(),
                "month_end": month_end.isoformat(),
            },
            "schedule": [
                event
                for event in schedule_range
                if event["date"] == today_date.isoformat()
            ],
            "schedule_events": schedule_range,
            "recent": recent[-250:],
            "camera": {
                "enabled": bool(
                    self.config_data.get("remote_camera_enabled", False)
                ),
                "status": self.camera_status_var.get(),
                "camera_index": int(
                    self.config_data.get("remote_camera_index", 0)
                ),
                "audio": False,
                "mode": "secure-remote-mjpeg",
                "private_url": str(
                    self.config_data.get("tailscale_camera_url", "")
                ).rstrip("/"),
                "password_protected": has_camera_password(
                    self.config_data
                ),
                "tailscale_identity_required": bool(
                    self.config_data.get(
                        "tailscale_camera_require_identity",
                        True,
                    )
                ),
                "max_session_minutes": int(
                    self.config_data.get(
                        "remote_camera_session_minutes",
                        10,
                    )
                ),
            },
            "pixela": {
                "username": str(self.config_data.get("username", "")),
                "graph_id": str(self.config_data.get("graph_id", "")),
                "status": self.sync_status_var.get(),
            },
        }
    def _monitor_graph_png(self) -> bytes | None:
        now = time.monotonic()
        if self._monitor_graph_cache and now - self._monitor_graph_cache_at < 60:
            return self._monitor_graph_cache
        if not self.pixela.username or self.pixela.username == "change-me" or resvg_py is None:
            return None
        try:
            svg_bytes, _status, _duration = self.pixela.graph_svg(mode=None)
            image = resvg_py.svg_to_bytes(
                svg_string=svg_bytes.decode("utf-8"),
                width=1100,
            )
            self._monitor_graph_cache = image
            self._monitor_graph_cache_at = now
            return image
        except Exception:
            return self._monitor_graph_cache

    def _start_monitor_server(self) -> None:
        if not bool(self.config_data.get("monitor_enabled", True)):
            return
        try:
            self._monitor_server = MonitorServer(
                self._monitor_payload,
                self._monitor_graph_png,
                port=int(self.config_data.get("monitor_port", 8765)),
            )
            self._monitor_server.start()
            self.local_status_var.set(f"Web dashboard: {self._monitor_server.url}")
            if bool(self.config_data.get("web_dashboard_auto_open", True)):
                self.after(700, self.open_web_dashboard)
        except OSError as exc:
            self.local_status_var.set(f"Web dashboard unavailable: {exc}")
            self._monitor_server = None

    def open_web_dashboard(self) -> None:
        if self._monitor_server is None:
            messagebox.showinfo(
                "Web dashboard",
                "The web dashboard server is not running. Check monitor settings or restart the app.",
            )
            return
        webbrowser.open(self._monitor_server.local_url)

    def _set_cloud_status_threadsafe(self, message: str) -> None:
        self.cloud_status_detail = message
        try:
            self.after(0, lambda: self.cloud_status_var.set(message[:82]))
        except Exception:
            pass

    def _configure_cloud_publisher(self) -> None:
        if self.cloud_publisher is not None:
            self.cloud_publisher.stop()
            self.cloud_publisher = None

        if not bool(self.config_data.get("cloud_dashboard_enabled", False)):
            self.cloud_status_var.set("Cloud dashboard: disabled")
            return

        url = str(self.config_data.get("cloud_dashboard_url", "")).strip()
        write_key = str(
            self.config_data.get("cloud_desktop_write_key", "")
        ).strip()
        if not url or not write_key:
            self.cloud_status_var.set("Cloud dashboard: configuration incomplete")
            return

        self.cloud_publisher = CloudDashboardPublisher(
            url,
            write_key,
            status_callback=self._set_cloud_status_threadsafe,
        )
        if not self.cloud_publisher.configured:
            self.cloud_status_var.set(
                "Cloud dashboard: use an HTTPS URL and a write key"
            )
            self.cloud_publisher.stop()
            self.cloud_publisher = None
            return

        self.cloud_status_var.set("Cloud dashboard: ready to upload")

    def _configure_cloud_syncer(self) -> None:
        if self.cloud_syncer is not None:
            self.cloud_syncer.stop()
            self.cloud_syncer = None
        if not bool(self.config_data.get("cloud_dashboard_enabled", False)):
            return
        if not bool(self.config_data.get("cloud_two_way_sync_enabled", True)):
            return
        url = str(self.config_data.get("cloud_dashboard_url", "")).strip()
        write_key = str(self.config_data.get("cloud_desktop_write_key", "")).strip()
        if not url or not write_key:
            return
        self.cloud_syncer = CloudStateSynchronizer(
            url,
            write_key,
            status_callback=self._set_cloud_status_threadsafe,
            result_callback=self._handle_cloud_sync_result_threadsafe,
        )
        if not self.cloud_syncer.configured:
            self.cloud_syncer.stop()
            self.cloud_syncer = None

    def _handle_cloud_sync_result_threadsafe(self, result: dict[str, Any]) -> None:
        try:
            self.after(0, lambda: self._apply_cloud_sync_result(result))
        except Exception:
            pass

    def _apply_cloud_sync_result(self, result: dict[str, Any]) -> None:
        echoed_client_time = str(result.get("client_sent_at") or "").strip()
        server_time = str(result.get("server_time") or "").strip()
        if echoed_client_time:
            self._cloud_client_since = echoed_client_time
        if server_time:
            self._cloud_server_since = server_time
        schedule_changed = self.schedule_store.merge_remote(result.get("schedule", []))
        sessions_added = self.store.import_cloud_sessions(result.get("sessions", []))
        timer_page = self.pages.get("Timer")
        timer_changed = False
        if isinstance(timer_page, TimerPage):
            timer_changed = timer_page.apply_cloud_timer(
                result.get("timer") or {},
                conflict=bool(result.get("timer_conflict", False)),
            )
        if schedule_changed or sessions_added or timer_changed:
            self.refresh_data_views()
            self.publish_cloud_snapshot_now()
        if result.get("timer_conflict"):
            self.local_status_var.set(
                "Cloud timer conflict: local and phone timers were both preserved"
            )
        elif sessions_added:
            self.local_status_var.set(
                f"Imported {sessions_added} cloud session(s)"
            )
            if self.pixela.configured:
                self.sync_now()

    def _cloud_sync_payload(self) -> dict[str, Any]:
        timer_page = self.pages.get("Timer")
        timer = (
            timer_page.cloud_sync_payload()
            if isinstance(timer_page, TimerPage)
            else {}
        )
        client_sent_at = datetime.now(timezone.utc).isoformat()
        return {
            "device_id": str(self.config_data.get("cloud_device_id", "desktop")),
            "app_version": APP_VERSION,
            "client_sent_at": client_sent_at,
            "server_since": self._cloud_server_since,
            "timer": timer,
            "schedule": self.schedule_store.all_sync_rows(
                updated_after=self._cloud_client_since,
            ),
            "sessions": self.store.sessions_for_cloud(
                limit=750,
                updated_after=self._cloud_client_since,
            ),
        }

    def _cloud_sync_tick(self) -> None:
        timer_page = self.pages.get("Timer")
        running = isinstance(timer_page, TimerPage) and timer_page.state.running
        if self.cloud_syncer is not None:
            self.cloud_syncer.submit(self._cloud_sync_payload())
        key = "cloud_sync_running_seconds" if running else "cloud_sync_idle_seconds"
        default = 5 if running else 30
        try:
            seconds = max(3, int(self.config_data.get(key, default)))
        except (TypeError, ValueError):
            seconds = default
        self.after(seconds * 1000, self._cloud_sync_tick)

    def _cloud_dashboard_tick(self) -> None:
        timer_page = self.pages.get("Timer")
        running = (
            isinstance(timer_page, TimerPage)
            and bool(timer_page.state.running)
        )
        if self.cloud_publisher is not None:
            self.cloud_publisher.submit(self._monitor_payload())

        key = (
            "cloud_running_update_seconds"
            if running
            else "cloud_idle_update_seconds"
        )
        default_seconds = 4 if running else 30
        try:
            seconds = max(2, int(self.config_data.get(key, default_seconds)))
        except (TypeError, ValueError):
            seconds = default_seconds
        if bool(self.config_data.get("remote_camera_enabled", False)):
            seconds = min(seconds, 8)
        self.after(seconds * 1000, self._cloud_dashboard_tick)

    def publish_cloud_snapshot_now(self) -> None:
        if self.cloud_publisher is not None:
            self.cloud_publisher.submit(self._monitor_payload())

    def _set_camera_status_threadsafe(self, message: str) -> None:
        self.camera_status_detail = message
        try:
            self.after(0, lambda: self.camera_status_var.set(message[:82]))
        except Exception:
            pass

    def _camera_allowed_origins(self) -> str:
        """Return every browser origin allowed to call the private camera API.

        The built-in camera viewer performs same-origin POST requests. Browsers
        still attach an Origin header to those POST requests, so the configured
        camera URL itself must be included alongside any dashboard origin.
        """
        from urllib.parse import urlparse

        origins: list[str] = []

        def add_values(value: object) -> None:
            text = str(value or "")
            for part in text.replace(",", "\n").splitlines():
                candidate = part.strip().rstrip("/")
                if not candidate:
                    continue
                try:
                    parsed = urlparse(candidate)
                except Exception:
                    continue
                if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
                    continue
                origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
                if origin not in origins:
                    origins.append(origin)

        # Always allow the configured camera site itself. This is required for
        # /viewer -> /api/unlock, because POST fetches include an Origin header
        # even when the request is same-origin.
        add_values(self.config_data.get("tailscale_camera_url", ""))

        # Keep the manually configured dashboard origin(s). Comma-separated and
        # newline-separated values are both supported.
        add_values(self.config_data.get("tailscale_camera_allowed_origin", ""))

        # Also allow the configured cloud dashboard. No third-party origin is
        # trusted automatically; each self-hosted installation must list its own.
        add_values(self.config_data.get("cloud_dashboard_url", ""))

        return ", ".join(origins)

    def _configure_remote_camera(self) -> None:
        if self.camera_server is not None:
            self.camera_server.stop()
            self.camera_server = None

        enabled = bool(
            self.config_data.get("remote_camera_enabled", False)
        )
        self.camera_enabled_var.set(enabled)

        if enabled and not has_camera_password(self.config_data):
            enabled = False
            self.config_data["remote_camera_enabled"] = False
            self.camera_enabled_var.set(False)
            self.save_config_data()
            self.camera_status_var.set(
                "Private camera: set a viewer password in Settings"
            )

        try:
            self.camera_server = TailscaleCameraServer(
                port=int(
                    self.config_data.get(
                        "tailscale_camera_port",
                        8788,
                    )
                ),
                camera_index=int(
                    self.config_data.get("remote_camera_index", 0)
                ),
                width=int(
                    self.config_data.get("remote_camera_width", 960)
                ),
                height=int(
                    self.config_data.get("remote_camera_height", 540)
                ),
                fps=int(
                    self.config_data.get("remote_camera_fps", 10)
                ),
                jpeg_quality=int(
                    self.config_data.get(
                        "remote_camera_jpeg_quality",
                        72,
                    )
                ),
                idle_seconds=int(
                    self.config_data.get(
                        "remote_camera_idle_seconds",
                        15,
                    )
                ),
                session_minutes=int(
                    self.config_data.get(
                        "remote_camera_session_minutes",
                        10,
                    )
                ),
                password_config=self.config_data,
                allowed_origins=self._camera_allowed_origins(),
                require_identity=bool(
                    self.config_data.get(
                        "tailscale_camera_require_identity",
                        True,
                    )
                ),
                allowed_users=str(
                    self.config_data.get(
                        "tailscale_camera_allowed_users",
                        "",
                    )
                ),
                enabled=enabled,
                status_callback=self._set_camera_status_threadsafe,
            )
            self.camera_server.start()
        except Exception as exc:
            self.camera_server = None
            self.camera_enabled_var.set(False)
            self.config_data["remote_camera_enabled"] = False
            self.save_config_data()
            self.camera_status_var.set(
                "Private camera: local server failed"
            )
            self.camera_status_detail = str(exc)
            return

        private_url = str(
            self.config_data.get("tailscale_camera_url", "")
        ).strip()
        if enabled and private_url:
            self.camera_status_var.set(
                "Private camera: enabled · waiting for remote viewer"
            )
        elif enabled:
            self.camera_status_var.set(
                "Private camera: local server ready · configure a secure route"
            )
        else:
            self.camera_status_var.set("Private camera: disabled")

    def toggle_remote_camera(self) -> None:
        enabled = bool(self.camera_enabled_var.get())
        if enabled and not has_camera_password(self.config_data):
            self.camera_enabled_var.set(False)
            messagebox.showerror(
                "Camera password required",
                "Set a camera viewer password in Settings before enabling "
                "the private camera.",
            )
            return

        self.config_data["remote_camera_enabled"] = enabled
        self.save_config_data()

        settings_page = self.pages.get("Settings")
        if isinstance(settings_page, SettingsPage):
            variable = settings_page.vars.get("remote_camera_enabled")
            if isinstance(variable, ctk.BooleanVar):
                variable.set(enabled)

        if self.camera_server is not None:
            self.camera_server.set_enabled(enabled)
        else:
            self._configure_remote_camera()

        self.publish_cloud_snapshot_now()

    def test_local_camera(self) -> None:
        def worker() -> None:
            try:
                width, height = test_camera(
                    int(
                        self.config_data.get(
                            "remote_camera_index",
                            0,
                        )
                    ),
                    int(
                        self.config_data.get(
                            "remote_camera_width",
                            960,
                        )
                    ),
                    int(
                        self.config_data.get(
                            "remote_camera_height",
                            540,
                        )
                    ),
                )
                message = (
                    f"Camera test succeeded: {width}×{height}. "
                    "The webcam was released after the test."
                )
                self.after(
                    0,
                    lambda: messagebox.showinfo(
                        "Camera test",
                        message,
                    ),
                )
            except Exception as exc:
                error = str(exc)
                self.after(
                    0,
                    lambda: messagebox.showerror(
                        "Camera test failed",
                        error,
                    ),
                )

        threading.Thread(target=worker, daemon=True).start()

    def configure_tailscale_camera(self) -> None:
        def worker() -> None:
            port = int(
                self.config_data.get(
                    "tailscale_camera_port",
                    8788,
                )
            )
            try:
                result = configure_serve(port)
                if result.approval_url:
                    self.after(
                        0,
                        lambda: webbrowser.open(
                            result.approval_url
                        ),
                    )
                    self.after(
                        0,
                        lambda: messagebox.showinfo(
                            "Tailscale approval required",
                            "A browser page was opened. Approve HTTPS/Serve, "
                            "then press this setup button again.",
                        ),
                    )
                    return

                self.config_data[
                    "tailscale_camera_url"
                ] = result.private_url
                self.save_config_data()
                self.publish_cloud_snapshot_now()

                settings_page = self.pages.get("Settings")
                if isinstance(settings_page, SettingsPage):
                    variable = settings_page.vars.get(
                        "tailscale_camera_url"
                    )
                    if variable is not None:
                        self.after(
                            0,
                            lambda: variable.set(
                                result.private_url
                            ),
                        )

                self.after(
                    0,
                    lambda: messagebox.showinfo(
                        "Tailscale camera ready",
                        "Tailscale Serve is configured.\n\n"
                        f"Private URL:\n{result.private_url}\n\n"
                        "Connect Tailscale on the viewer device, then use "
                        "the camera card inside the Cloudflare dashboard.",
                    ),
                )
            except TailscaleSetupError as exc:
                error = str(exc)
                self.after(
                    0,
                    lambda: messagebox.showerror(
                        "Tailscale setup failed",
                        error,
                    ),
                )

        threading.Thread(target=worker, daemon=True).start()

    def open_private_camera(self) -> None:
        url = str(
            self.config_data.get("tailscale_camera_url", "")
        ).strip()
        if not url:
            messagebox.showinfo(
                "Private camera URL",
                "Configure the camera public/private URL first.",
            )
            return
        webbrowser.open(url.rstrip("/") + "/viewer")

    def show_tailscale_serve_status(self) -> None:
        def worker() -> None:
            text = serve_status_text() or "No Serve configuration found."
            self.after(
                0,
                lambda: messagebox.showinfo(
                    "Tailscale Serve status",
                    text,
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def test_cloud_dashboard(self) -> None:
        self._configure_cloud_publisher()
        self._configure_cloud_syncer()
        if self.cloud_publisher is None:
            messagebox.showerror(
                "Cloud dashboard",
                "Enable the cloud dashboard and enter its HTTPS URL and desktop write key.",
            )
            return
        self.cloud_publisher.submit(self._monitor_payload())
        messagebox.showinfo(
            "Cloud dashboard",
            "A test snapshot was queued. Check the cloud status in the sidebar.",
        )

    def open_cloud_dashboard(self) -> None:
        url = str(
            self.config_data.get("cloud_dashboard_url", "")
        ).strip()
        if not url:
            messagebox.showinfo(
                "Cloud dashboard",
                "Enter the deployed Cloudflare Pages URL in Settings first.",
            )
            return
        webbrowser.open(url)

    def show_connection_details(self) -> None:
        window = ctk.CTkToplevel(self)
        window.title("Connection details")
        window.geometry("760x520")
        window.minsize(620, 420)
        window.transient(self)
        ctk.CTkLabel(window, text="Connection details", font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w", padx=20, pady=(18, 8))
        text = ctk.CTkTextbox(window, wrap="word")
        text.pack(fill="both", expand=True, padx=20, pady=(0, 12))
        details = (
            f"PIXELA\n{self.pixela_status_detail}\n\n"
            f"CLOUD DASHBOARD\n{self.cloud_status_detail}\n\n"
            f"PRIVATE CAMERA\n{self.camera_status_detail}\n\n"
            f"CLOUDFLARE TUNNEL\n{self.tunnel_status_detail}\n\n"
            f"LOCAL DASHBOARD\n{self.local_status_var.get()}\n"
        )
        text.insert("1.0", details)
        text.configure(state="disabled")
        ctk.CTkButton(window, text="Close", command=window.destroy).pack(anchor="e", padx=20, pady=(0, 18))

    def _set_tunnel_health_threadsafe(self, health: TunnelHealth) -> None:
        try:
            self.after(0, lambda: self._apply_tunnel_health(health))
        except Exception:
            pass

    def _apply_tunnel_health(self, health: TunnelHealth) -> None:
        previous = self._last_tunnel_overall
        self._last_tunnel_health = health
        self._last_tunnel_overall = health.overall

        if health.overall in {"healthy", "public_healthy_origin_uncertain"}:
            label = "Tunnel: online"
        elif health.overall == "access_protected":
            label = "Tunnel: reachable · Access protected"
        elif health.overall == "not_configured":
            label = "Tunnel: not configured"
        elif health.overall == "not_enabled":
            label = "Tunnel: camera monitoring disabled"
        elif health.overall == "route_reachable":
            label = "Tunnel: connected · health path missing"
        elif health.overall == "service_down":
            label = "Tunnel: Cloudflared service stopped"
        elif health.overall == "service_missing":
            label = "Tunnel: Cloudflared service missing"
        elif health.overall == "tunnel_down":
            label = "Tunnel: disconnected / 1033"
        elif health.overall == "origin_down":
            label = "Tunnel: origin unavailable"
        else:
            label = f"Tunnel: {health.headline}"

        self.tunnel_status_var.set(label[:88])
        self.tunnel_status_detail = (
            f"{health.headline}\n{health.detail}\n"
            f"Checked: {health.checked_at}\n"
            f"Cloudflared service: {'running' if health.service_running else 'not running'} "
            f"(PID {health.service_pid or '-'})\n"
            f"Local camera: HTTP {health.local_camera.status or '-'} · "
            f"{health.local_camera.elapsed_ms} ms\n"
            f"Public camera: HTTP {health.public_camera.status or '-'} · "
            f"{health.public_camera.elapsed_ms} ms\n"
            f"Dashboard health: HTTP {health.dashboard.status or '-'}\n"
            f"SYSTEM watchdog: {'installed' if health.watchdog_installed else 'not installed'}"
        )

        page = self.pages.get("Diagnostics")
        if isinstance(page, DiagnosticsPage):
            page.apply_health(health)

        notifications = bool(
            self.config_data.get("tunnel_notifications_enabled", True)
        )
        good = {"healthy", "public_healthy_origin_uncertain", "access_protected", "route_reachable"}
        ignored = {"", "unknown", "not_configured", "not_enabled"}
        if notifications and previous not in ignored and previous != health.overall:
            if previous in good and health.overall not in good:
                self._show_tunnel_toast(
                    "Cloudflare Tunnel needs attention",
                    f"{health.headline}\n{health.detail}",
                )
            elif previous not in good and health.overall in good:
                self._show_tunnel_toast(
                    "Cloudflare Tunnel recovered",
                    health.detail,
                )

    def _show_tunnel_toast(self, title: str, message: str) -> None:
        try:
            self.bell()
            popup = ctk.CTkToplevel(self)
            popup.title(title)
            popup.geometry("430x170")
            popup.resizable(False, False)
            popup.attributes("-topmost", True)
            popup.transient(self)
            ctk.CTkLabel(
                popup,
                text=title,
                font=ctk.CTkFont(size=17, weight="bold"),
            ).pack(anchor="w", padx=18, pady=(16, 6))
            ctk.CTkLabel(
                popup,
                text=message,
                justify="left",
                wraplength=390,
            ).pack(anchor="w", padx=18, pady=(0, 10))
            ctk.CTkButton(
                popup,
                text="Open diagnostics",
                width=150,
                command=lambda: (
                    popup.destroy(),
                    self.show_page("Diagnostics"),
                ),
            ).pack(anchor="e", padx=18, pady=(0, 14))
            popup.after(9000, lambda: popup.winfo_exists() and popup.destroy())
        except Exception:
            pass

    def refresh_tunnel_health(self) -> None:
        self.tunnel_status_var.set("Tunnel: checking…")
        self.tunnel_monitor.request_check()

    def _launch_project_tool(self, filename: str) -> None:
        path = PROJECT_ROOT / filename
        if not path.exists():
            messagebox.showerror(
                "Tool not found",
                f"The required file is missing:\n{path}",
            )
            return
        if not sys.platform.startswith("win"):
            messagebox.showinfo(
                "Windows tool",
                f"Run this file on Windows:\n{path}",
            )
            return
        try:
            os.startfile(str(path))  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("Could not start tool", str(exc))

    def run_full_diagnosis(self) -> None:
        self._launch_project_tool("FOCUS_STUDIO_DIAGNOSIS.bat")

    def run_tunnel_repair(self) -> None:
        self._launch_project_tool("REPAIR_CLOUDFLARE_TUNNEL.bat")

    def install_tunnel_watchdog(self) -> None:
        self._launch_project_tool("INSTALL_TUNNEL_WATCHDOG.bat")

    def remove_tunnel_watchdog(self) -> None:
        self._launch_project_tool("REMOVE_TUNNEL_WATCHDOG.bat")

    def make_pixela_client(self) -> PixelaClient:
        return PixelaClient(
            str(self.config_data.get("username", "")),
            str(self.config_data.get("token", "")),
            str(self.config_data.get("graph_id", "")),
        )

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, width=312, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_rowconfigure(10, weight=1)
        sidebar.grid_propagate(False)

        ctk.CTkLabel(
            sidebar,
            text="MOJJSS LIVE ACTIVITY",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).grid(row=0, column=0, padx=18, pady=(24, 4), sticky="w")
        ctk.CTkLabel(
            sidebar,
            text="computer engineering · research",
            text_color=("#64748b", "#94a3b8"),
            font=ctk.CTkFont(size=11),
        ).grid(row=1, column=0, padx=18, pady=(0, 10), sticky="w")

        credentials = ctk.CTkFrame(sidebar, fg_color="transparent")
        credentials.grid(row=2, column=0, padx=14, pady=(0, 12), sticky="ew")
        credentials.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(
            credentials,
            text="mojsadafi.ir",
            height=29,
            font=ctk.CTkFont(size=11),
            fg_color=("#e2e8f0", "#28313d"),
            text_color=("#1f2937", "#e5e7eb"),
            hover_color=("#cbd5e1", "#3b4756"),
            command=lambda: webbrowser.open("https://mojsadafi.ir"),
        ).grid(row=0, column=0, padx=(0, 3), sticky="ew")
        ctk.CTkButton(
            credentials,
            text="GitHub",
            height=29,
            font=ctk.CTkFont(size=11),
            fg_color=("#e2e8f0", "#28313d"),
            text_color=("#1f2937", "#e5e7eb"),
            hover_color=("#cbd5e1", "#3b4756"),
            command=lambda: webbrowser.open("https://github.com/mojjss"),
        ).grid(row=0, column=1, padx=(3, 0), sticky="ew")

        page_names = [
            "Timer",
            "Schedule",
            "Sessions",
            "Analytics",
            "Pixela",
            "Diagnostics",
            "Settings",
        ]
        for index, name in enumerate(page_names, start=3):
            button = ctk.CTkButton(
                sidebar,
                text=name,
                anchor="w",
                height=40,
                corner_radius=8,
                fg_color="transparent",
                text_color=("#1f2937", "#e5e7eb"),
                hover_color=("#e2e8f0", "#28313d"),
                command=lambda page=name: self.show_page(page),
            )
            button.grid(row=index, column=0, padx=14, pady=3, sticky="ew")
            self.nav_buttons[name] = button

        status_card = ctk.CTkFrame(sidebar, corner_radius=12)
        status_card.grid(row=11, column=0, padx=14, pady=14, sticky="sew")
        ctk.CTkLabel(
            status_card,
            textvariable=self.sync_status_var,
            wraplength=258,
            justify="left",
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=12, pady=(12, 4))
        ctk.CTkLabel(
            status_card,
            textvariable=self.local_status_var,
            wraplength=258,
            justify="left",
            text_color=("#64748b", "#94a3b8"),
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=12, pady=(0, 4))
        ctk.CTkLabel(
            status_card,
            textvariable=self.cloud_status_var,
            wraplength=258,
            justify="left",
            text_color=("#64748b", "#94a3b8"),
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=12, pady=(0, 4))
        ctk.CTkLabel(
            status_card,
            textvariable=self.camera_status_var,
            wraplength=258,
            justify="left",
            text_color=("#64748b", "#94a3b8"),
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=12, pady=(0, 5))
        ctk.CTkLabel(
            status_card,
            textvariable=self.tunnel_status_var,
            wraplength=258,
            justify="left",
            text_color=("#64748b", "#94a3b8"),
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=12, pady=(0, 5))
        ctk.CTkSwitch(
            status_card,
            text="Allow private camera",
            variable=self.camera_enabled_var,
            command=self.toggle_remote_camera,
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=12, pady=(0, 8))
        ctk.CTkButton(
            status_card,
            text="Tunnel diagnostics",
            height=30,
            command=lambda: self.show_page("Diagnostics"),
        ).pack(fill="x", padx=12, pady=(0, 6))
        ctk.CTkButton(
            status_card,
            text="Connection details",
            height=30,
            fg_color=("#e2e8f0", "#2b3542"),
            text_color=("#1f2937", "#e5e7eb"),
            hover_color=("#cbd5e1", "#3b4756"),
            command=self.show_connection_details,
        ).pack(fill="x", padx=12, pady=(0, 6))
        ctk.CTkButton(
            status_card,
            text="Open private camera",
            height=30,
            command=self.open_private_camera,
        ).pack(fill="x", padx=12, pady=(0, 6))
        ctk.CTkButton(
            status_card,
            text="Open local dashboard",
            height=30,
            command=self.open_web_dashboard,
        ).pack(fill="x", padx=12, pady=(0, 6))
        ctk.CTkButton(
            status_card,
            text="Open cloud dashboard",
            height=30,
            command=self.open_cloud_dashboard,
        ).pack(fill="x", padx=12, pady=(0, 12))

    def _build_content(self) -> None:
        host = ctk.CTkFrame(self, corner_radius=0, fg_color=("#f1f5f9", "#111827"))
        host.grid(row=0, column=1, sticky="nsew")
        host.grid_columnconfigure(0, weight=1)
        host.grid_rowconfigure(0, weight=1)

        self.pages = {
            "Timer": TimerPage(host, self),
            "Schedule": SchedulePage(host, self),
            "Sessions": SessionsPage(host, self),
            "Analytics": AnalyticsPage(host, self),
            "Pixela": PixelaPage(host, self),
            "Diagnostics": DiagnosticsPage(host, self),
            "Settings": SettingsPage(host, self),
        }
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew")

    def show_page(self, name: str) -> None:
        self.active_page = name
        self.pages[name].tkraise()
        for page_name, button in self.nav_buttons.items():
            if page_name == name:
                button.configure(
                    fg_color=("#dbeafe", "#1e3a5f"),
                    text_color=("#1d4ed8", "#bfdbfe"),
                )
            else:
                button.configure(
                    fg_color="transparent",
                    text_color=("#1f2937", "#e5e7eb"),
                )
        refresh = getattr(self.pages[name], "refresh", None)
        if callable(refresh):
            refresh()

    def refresh_data_views(self) -> None:
        for name in ("Timer", "Schedule", "Sessions", "Analytics", "Pixela"):
            refresh = getattr(self.pages.get(name), "refresh", None)
            if callable(refresh):
                refresh()

    def save_config_data(self) -> None:
        save_config(CONFIG_PATH, self.config_data)
        self.pixela = self.make_pixela_client()
        self.zone = ZoneInfo(self.config_data["timezone"])

    def sync_now(self, callback=None) -> None:
        if not self.pixela.configured:
            self.sync_status_var.set("Pixela: configuration incomplete")
            if callback:
                callback(False, "Pixela configuration is incomplete.")
            return

        self.sync_status_var.set("Pixela: syncing…")

        def worker() -> None:
            started = datetime.now(timezone.utc)
            started_perf = time.perf_counter()
            dates = self.store.unsynced_dates()
            synced = 0
            http_status: int | None = None
            message = "Nothing new to sync."
            status = "success"
            try:
                for date in dates:
                    total = self.store.total_for_date(date)
                    result = self.pixela.put_daily_total(date, total)
                    http_status = result.http_status
                    self.store.mark_date_synced(date)
                    synced += 1
                if dates:
                    message = f"Synced {synced} day(s)."
            except Exception as exc:
                status = "error"
                message = str(exc)
                http_status = getattr(exc, "http_status", http_status)
            finished = datetime.now(timezone.utc)
            self.store.add_sync_run(
                {
                    "started_at": started.isoformat(),
                    "finished_at": finished.isoformat(),
                    "status": status,
                    "dates_attempted": len(dates),
                    "dates_synced": synced,
                    "http_status": http_status,
                    "duration_ms": int((time.perf_counter() - started_perf) * 1000),
                    "message": message,
                }
            )
            self.store.export_readable_copy()

            def done() -> None:
                self.pixela_status_detail = message
                if status == "success":
                    self.sync_status_var.set(f"Pixela: {message[:68]}")
                else:
                    self.sync_status_var.set("Pixela: sync failed — see details")
                self.refresh_data_views()
                if callback:
                    callback(status == "success", message)

            self.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def background_connection_check(self) -> None:
        if not self.pixela.configured:
            self.sync_status_var.set("Pixela: configuration incomplete")
            return

        def worker() -> None:
            try:
                result = self.pixela.test_connection()
                detail = f"Connected successfully. HTTP {result.http_status}."
                text = f"Pixela: connected · HTTP {result.http_status}"
            except Exception as exc:
                detail = str(exc)
                text = "Pixela: offline — see connection details"
            self.pixela_status_detail = detail
            self.after(0, lambda: self.sync_status_var.set(text))

        threading.Thread(target=worker, daemon=True).start()

    def on_close(self) -> None:
        timer_page = self.pages.get("Timer")
        if isinstance(timer_page, TimerPage) and timer_page.state.running:
            close = messagebox.askyesno(
                "Timer running",
                "A timer is active. Close the app? The timer state will be preserved and can resume when the app opens again.",
            )
            if not close:
                return
            timer_page._update_clock()
            timer_page._persist_timer_state()
        if self._monitor_server is not None:
            self._monitor_server.stop()
        if self.cloud_publisher is not None:
            self.cloud_publisher.stop()
        if self.cloud_syncer is not None:
            self.cloud_syncer.stop()
        if self.camera_server is not None:
            self.camera_server.stop()
        self.tunnel_monitor.stop()
        self.destroy()


class PageBase(ctk.CTkFrame):
    def __init__(self, master, app: FocusApp, title: str, subtitle: str):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        heading = ctk.CTkFrame(self, fg_color="transparent")
        heading.grid(row=0, column=0, padx=26, pady=(22, 14), sticky="ew")
        ctk.CTkLabel(
            heading,
            text=title,
            font=ctk.CTkFont(size=26, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            heading,
            text=subtitle,
            text_color=("#64748b", "#94a3b8"),
        ).pack(anchor="w", pady=(2, 0))


class TimerPage(PageBase):
    def __init__(self, master, app: FocusApp):
        super().__init__(
            master,
            app,
            "Focus timer",
            "Choose one concrete task, start small, and let the app handle the record keeping.",
        )
        self.state = TimerState(
            duration_seconds=int(app.config_data["focus_minutes"]) * 60,
            remaining_seconds=int(app.config_data["focus_minutes"]) * 60,
        )
        self.completed_in_cycle = 0

        self.task_var = ctk.StringVar(value="")
        self.category_var = ctk.StringVar(value="Research")
        self.mode_var = ctk.StringVar(value="Focus")
        self.focus_sync_var = ctk.BooleanVar(value=True)
        self.focus_sync_note_var = ctk.StringVar(
            value="Included in focused time and the Pixela graph"
        )
        self.time_var = ctk.StringVar(value=self.format_seconds(self.state.remaining_seconds))
        self.timer_status_var = ctk.StringVar(value="Ready")
        self.today_var = ctk.StringVar(value="")
        self.cycle_var = ctk.StringVar(value="")
        self.custom_minutes_var = ctk.StringVar(value=str(app.config_data["focus_minutes"]))
        self._last_timer_persist_at = 0.0

        self._build()
        self._restore_timer_state()
        self.refresh()
        self.after(200, self.tick)

    def _build(self) -> None:
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, padx=26, pady=(0, 24), sticky="nsew")
        body.grid_columnconfigure(0, weight=5)
        body.grid_columnconfigure(1, weight=3)
        body.grid_rowconfigure(0, weight=1)

        timer_card = ctk.CTkFrame(body, corner_radius=16)
        timer_card.grid(row=0, column=0, padx=(0, 12), sticky="nsew")
        timer_card.grid_columnconfigure(0, weight=1)

        form = ctk.CTkFrame(timer_card, fg_color="transparent")
        form.grid(row=0, column=0, padx=24, pady=(22, 8), sticky="ew")
        form.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkLabel(form, text="Current task", anchor="w").grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(form, text="Category", anchor="w").grid(row=0, column=1, padx=(12, 0), sticky="ew")
        self.task_combo = ctk.CTkComboBox(
            form,
            variable=self.task_var,
            values=[""],
            height=38,
        )
        self.task_combo.grid(row=1, column=0, pady=(4, 0), sticky="ew")
        self.category_combo = ctk.CTkComboBox(
            form,
            variable=self.category_var,
            values=self.app.config_data["categories"],
            height=38,
        )
        self.category_combo.grid(row=1, column=1, padx=(12, 0), pady=(4, 0), sticky="ew")

        focus_option = ctk.CTkFrame(form, fg_color="transparent")
        focus_option.grid(row=2, column=0, columnspan=2, pady=(12, 0), sticky="ew")
        self.focus_sync_switch = ctk.CTkSwitch(
            focus_option,
            text="Count toward focused time and sync to Pixela",
            variable=self.focus_sync_var,
            command=self._focus_sync_changed,
        )
        self.focus_sync_switch.pack(side="left")
        ctk.CTkLabel(
            focus_option,
            textvariable=self.focus_sync_note_var,
            text_color=("#64748b", "#94a3b8"),
            font=ctk.CTkFont(size=11),
        ).pack(side="left", padx=(12, 0))

        self.mode_segment = ctk.CTkSegmentedButton(
            timer_card,
            values=["Focus", "Flow", "Productive", "Personal", "Short Break", "Long Break"],
            variable=self.mode_var,
            command=self.change_mode,
        )
        self.mode_segment.grid(row=1, column=0, padx=24, pady=(10, 4))

        ctk.CTkLabel(
            timer_card,
            textvariable=self.time_var,
            font=ctk.CTkFont(size=72, weight="bold"),
        ).grid(row=2, column=0, pady=(12, 4))

        self.progress = ctk.CTkProgressBar(timer_card, height=12)
        self.progress.grid(row=3, column=0, padx=50, pady=(0, 12), sticky="ew")
        self.progress.set(0)

        preset_frame = ctk.CTkFrame(timer_card, fg_color="transparent")
        preset_frame.grid(row=4, column=0, padx=20, pady=6)
        for value in (5, 15, 25, 45, 60, 90):
            ctk.CTkButton(
                preset_frame,
                text=f"{value}m",
                width=58,
                height=32,
                fg_color=("#e2e8f0", "#2b3542"),
                text_color=("#1f2937", "#e5e7eb"),
                hover_color=("#cbd5e1", "#3b4756"),
                command=lambda minutes=value: self.set_duration(minutes),
            ).pack(side="left", padx=3)

        custom = ctk.CTkFrame(timer_card, fg_color="transparent")
        custom.grid(row=5, column=0, pady=(4, 8))
        ctk.CTkEntry(custom, textvariable=self.custom_minutes_var, width=72).pack(side="left", padx=(0, 6))
        ctk.CTkButton(custom, text="Set custom", width=92, command=self.set_custom_duration).pack(side="left")

        controls = ctk.CTkFrame(timer_card, fg_color="transparent")
        controls.grid(row=6, column=0, pady=(10, 12))
        self.start_button = ctk.CTkButton(
            controls,
            text="Start",
            width=150,
            height=46,
            font=ctk.CTkFont(size=16, weight="bold"),
            command=self.start_pause,
        )
        self.start_button.pack(side="left", padx=5)
        ctk.CTkButton(
            controls,
            text="Complete now",
            width=120,
            height=46,
            fg_color=("#0f766e", "#0f766e"),
            hover_color=("#115e59", "#115e59"),
            command=self.complete_early,
        ).pack(side="left", padx=5)
        ctk.CTkButton(
            controls,
            text="Reset",
            width=90,
            height=46,
            fg_color=("#64748b", "#475569"),
            hover_color=("#475569", "#334155"),
            command=self.reset_timer,
        ).pack(side="left", padx=5)
        ctk.CTkButton(
            controls,
            text="Copy activity summary",
            width=170,
            height=46,
            fg_color=("#1d4ed8", "#2563eb"),
            hover_color=("#1e40af", "#1d4ed8"),
            command=self.copy_activity_summary,
        ).pack(side="left", padx=5)

        ctk.CTkLabel(
            timer_card,
            textvariable=self.timer_status_var,
            text_color=("#64748b", "#94a3b8"),
        ).grid(row=7, column=0, pady=(0, 18))

        side = ctk.CTkFrame(body, corner_radius=16)
        side.grid(row=0, column=1, padx=(12, 0), sticky="nsew")
        side.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(side, text="Today", font=ctk.CTkFont(size=18, weight="bold")).grid(row=0, column=0, padx=20, pady=(22, 2), sticky="w")
        ctk.CTkLabel(
            side,
            textvariable=self.today_var,
            font=ctk.CTkFont(size=21, weight="bold"),
            justify="left",
        ).grid(row=1, column=0, padx=20, pady=(0, 4), sticky="w")
        self.goal_bar = ctk.CTkProgressBar(side, height=10)
        self.goal_bar.grid(row=2, column=0, padx=20, pady=(4, 16), sticky="ew")

        ctk.CTkLabel(side, text="Pomodoro cycle", font=ctk.CTkFont(size=15, weight="bold")).grid(row=3, column=0, padx=20, pady=(8, 2), sticky="w")
        ctk.CTkLabel(side, textvariable=self.cycle_var, text_color=("#475569", "#cbd5e1")).grid(row=4, column=0, padx=20, pady=(0, 14), sticky="w")

        ctk.CTkLabel(side, text="Low-friction routine", font=ctk.CTkFont(size=15, weight="bold")).grid(row=5, column=0, padx=20, pady=(12, 4), sticky="w")
        tips = (
            "1. Focus/Flow: deep work sent to Pixela.\n"
            "2. Productive: calls, meetings, email, admin.\n"
            "3. Personal: tracked but excluded from work totals.\n"
            "4. Review patterns later—not during the task."
        )
        ctk.CTkLabel(
            side,
            text=tips,
            justify="left",
            anchor="nw",
            wraplength=260,
            text_color=("#475569", "#cbd5e1"),
        ).grid(row=6, column=0, padx=20, pady=(0, 18), sticky="nw")

        ctk.CTkButton(side, text="Sync with Pixela", command=self.app.sync_now).grid(row=7, column=0, padx=20, pady=(8, 8), sticky="ew")
        ctk.CTkButton(
            side,
            text="Review session log",
            fg_color="transparent",
            border_width=1,
            text_color=("#1d4ed8", "#93c5fd"),
            command=lambda: self.app.show_page("Sessions"),
        ).grid(row=8, column=0, padx=20, pady=(0, 20), sticky="ew")

    @staticmethod
    def format_seconds(seconds: int) -> str:
        seconds = max(0, int(seconds))
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def refresh(self) -> None:
        tasks = self.app.store.distinct_tasks()
        self.task_combo.configure(values=tasks or [""])
        categories = sorted(set(self.app.config_data.get("categories", [])) | set(self.app.store.distinct_categories()))
        self.category_combo.configure(values=categories or ["General"])

        summary = self.app.store.today_summary()
        goal = max(1, int(self.app.config_data.get("daily_goal_minutes", 120)))
        self.today_var.set(
            f"Focus: {summary['focus_minutes']} min · {summary['focus_sessions']} session(s)\n"
            f"Other productive: {summary['other_productive_minutes']} min"
        )
        self.goal_bar.set(min(1.0, summary["focus_minutes"] / goal))
        total_cycle = max(1, int(self.app.config_data.get("sessions_before_long_break", 4)))
        self.cycle_var.set(f"{self.completed_in_cycle % total_cycle} of {total_cycle} focus blocks complete")

    def _touch_timer(self, status: str | None = None) -> None:
        if status is not None:
            self.state.sync_status = status
        self.state.revision += 1
        self.state.updated_at = datetime.now(timezone.utc)
        self._persist_timer_state()

    def _persist_timer_state(self) -> None:
        try:
            payload = self.cloud_sync_payload(update_clock=False)
            payload["saved_at"] = datetime.now(timezone.utc).isoformat()
            TIMER_STATE_PATH.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _restore_timer_state(self) -> None:
        if not TIMER_STATE_PATH.exists():
            return
        try:
            raw = json.loads(TIMER_STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict) or not raw.get("session_id"):
            return
        status = str(raw.get("status") or "idle")
        if status not in {"running", "paused"}:
            return
        mode = str(raw.get("mode") or "Focus")
        self.state.session_id = str(raw.get("session_id"))
        self.state.revision = int(raw.get("revision") or 0)
        self.state.sync_status = status
        self.state.mode = mode
        self.state.duration_seconds = max(0, int(raw.get("duration_seconds") or 0))
        self.state.elapsed_seconds = max(0, int(raw.get("elapsed_seconds") or 0))
        self.state.remaining_seconds = max(0, int(raw.get("remaining_seconds") or 0))
        self.state.running = True
        self.state.paused = status == "paused"
        try:
            self.state.started_at = datetime.fromisoformat(str(raw.get("started_at")))
            if self.state.started_at.tzinfo is None:
                self.state.started_at = self.state.started_at.replace(tzinfo=self.app.zone)
        except (TypeError, ValueError):
            self.state.started_at = datetime.now(self.app.zone) - timedelta(
                seconds=self.state.elapsed_seconds
            )
        try:
            updated_at = datetime.fromisoformat(str(raw.get("updated_at")))
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            self.state.updated_at = updated_at.astimezone(timezone.utc)
        except (TypeError, ValueError):
            self.state.updated_at = datetime.now(timezone.utc)
        try:
            saved_at = datetime.fromisoformat(str(raw.get("saved_at") or raw.get("updated_at")))
            if saved_at.tzinfo is None:
                saved_at = saved_at.replace(tzinfo=timezone.utc)
            if status == "running":
                offline = max(0, int((datetime.now(timezone.utc) - saved_at.astimezone(timezone.utc)).total_seconds()))
                self.state.elapsed_seconds += offline
                if mode not in {"Flow", "Productive", "Personal"}:
                    self.state.remaining_seconds = max(
                        0, self.state.duration_seconds - self.state.elapsed_seconds
                    )
        except (TypeError, ValueError):
            pass
        self.state.monotonic_anchor = time.monotonic() if not self.state.paused else None
        self.task_var.set(str(raw.get("task") or ""))
        self.category_var.set(str(raw.get("category") or "Research"))
        self.mode_var.set(mode)
        self.focus_sync_var.set(bool(raw.get("counts_toward_focus")))
        self.start_button.configure(text="Resume" if self.state.paused else "Pause")
        self.timer_status_var.set("Paused" if self.state.paused else f"{mode} restored")
        self._refresh_timer_display()

    def _refresh_timer_display(self) -> None:
        count_up = self.state.mode in {"Flow", "Productive", "Personal"}
        display = self.state.elapsed_seconds if count_up else self.state.remaining_seconds
        self.time_var.set(self.format_seconds(display))
        if count_up:
            self.progress.set(0)
        else:
            duration = max(1, self.state.duration_seconds)
            self.progress.set(min(1.0, self.state.elapsed_seconds / duration))

    def cloud_sync_payload(self, *, update_clock: bool = True) -> dict[str, Any]:
        if update_clock and self.state.running and not self.state.paused:
            self._update_clock()
        status = self.state.sync_status
        if self.state.running:
            status = "paused" if self.state.paused else "running"
        return {
            "session_id": self.state.session_id,
            "revision": self.state.revision,
            "status": status,
            "running": self.state.running,
            "paused": self.state.paused,
            "task": self.task_var.get().strip(),
            "category": self.category_var.get().strip(),
            "mode": self.state.mode,
            "counts_toward_focus": bool(self.focus_sync_var.get()),
            "duration_seconds": int(self.state.duration_seconds),
            "elapsed_seconds": int(self.state.elapsed_seconds),
            "remaining_seconds": int(self.state.remaining_seconds),
            "started_at": self.state.started_at.astimezone(timezone.utc).isoformat()
                if self.state.started_at else None,
            "running_since": datetime.now(timezone.utc).isoformat()
                if self.state.running and not self.state.paused else None,
            "updated_at": (self.state.updated_at or datetime.now(timezone.utc)).isoformat(),
            "timezone": str(self.app.config_data.get("timezone", "UTC")),
        }

    def apply_cloud_timer(self, remote: dict[str, Any], *, conflict: bool = False) -> bool:
        if conflict or not isinstance(remote, dict):
            return False
        remote_id = str(remote.get("session_id") or "")
        remote_status = str(remote.get("status") or "idle")
        remote_active = remote_status in {"running", "paused"}
        same = bool(remote_id and remote_id == self.state.session_id)
        if self.state.running and remote_active and not same:
            return False
        if self.state.running and same and str(remote.get("source")) != "web":
            return False
        if not remote_id:
            return False

        if remote_active:
            self.state.session_id = remote_id
            self.state.revision = int(remote.get("revision") or 0)
            self.state.sync_status = remote_status
            self.state.mode = str(remote.get("mode") or "Focus")
            self.state.duration_seconds = max(0, int(remote.get("duration_seconds") or 0))
            self.state.elapsed_seconds = max(0, int(remote.get("elapsed_seconds") or 0))
            self.state.remaining_seconds = max(0, int(remote.get("remaining_seconds") or 0))
            self.state.running = True
            self.state.paused = remote_status == "paused"
            try:
                started = datetime.fromisoformat(str(remote.get("started_at")))
                self.state.started_at = started.astimezone(self.app.zone)
            except (TypeError, ValueError):
                self.state.started_at = datetime.now(self.app.zone) - timedelta(
                    seconds=self.state.elapsed_seconds
                )
            self.state.monotonic_anchor = time.monotonic() if not self.state.paused else None
            self.task_var.set(str(remote.get("task") or ""))
            self.category_var.set(str(remote.get("category") or "Research"))
            self.mode_var.set(self.state.mode)
            self.focus_sync_var.set(bool(remote.get("counts_toward_focus")))
            self.start_button.configure(text="Resume" if self.state.paused else "Pause")
            self.timer_status_var.set(
                "Paused from phone" if self.state.paused else "Running from phone"
            )
            self._refresh_timer_display()
            self._touch_timer(remote_status)
            return True

        if same and remote_status in {"completed", "canceled"}:
            self.state.running = False
            self.state.paused = False
            self.state.monotonic_anchor = None
            self.state.sync_status = remote_status
            self.start_button.configure(text="Start")
            self.timer_status_var.set(
                "Completed from phone" if remote_status == "completed" else "Canceled from phone"
            )
            self._refresh_timer_display()
            self._persist_timer_state()
            return True
        return False

    def _queue_two_way_sync(self) -> None:
        if self.app.cloud_syncer is not None:
            self.app.cloud_syncer.submit(self.app._cloud_sync_payload())

    def set_duration(self, minutes: int) -> None:
        if self.state.running:
            messagebox.showinfo("Timer active", "Reset or finish the current timer before changing its length.")
            return
        self.state.duration_seconds = int(minutes) * 60
        self.state.remaining_seconds = self.state.duration_seconds
        self.state.elapsed_seconds = 0
        self.custom_minutes_var.set(str(minutes))
        self.time_var.set(self.format_seconds(self.state.remaining_seconds))
        self.progress.set(0)
        self.timer_status_var.set(f"Ready for {minutes} minutes")
        self._touch_timer("idle")

    def set_custom_duration(self) -> None:
        try:
            minutes = int(self.custom_minutes_var.get())
        except ValueError:
            messagebox.showerror("Invalid duration", "Enter a whole number of minutes.")
            return
        if not 1 <= minutes <= 600:
            messagebox.showerror("Invalid duration", "Choose a value from 1 to 600 minutes.")
            return
        self.set_duration(minutes)

    def _focus_sync_changed(self) -> None:
        if self.focus_sync_var.get():
            self.focus_sync_note_var.set(
                "Included in focused time and the Pixela graph"
            )
        else:
            self.focus_sync_note_var.set(
                "Saved locally only; excluded from focused-time totals"
            )

    def change_mode(self, mode: str) -> None:
        if self.state.running:
            self.mode_var.set(self.state.mode)
            messagebox.showinfo(
                "Timer active",
                "Finish or reset the current timer before changing mode.",
            )
            return

        self.state.mode = mode
        durations = {
            "Focus": int(self.app.config_data["focus_minutes"]),
            "Short Break": int(self.app.config_data["short_break_minutes"]),
            "Long Break": int(self.app.config_data["long_break_minutes"]),
        }
        count_up_modes = {"Flow", "Productive", "Personal"}
        loggable_modes = {"Focus", "Flow", "Productive", "Personal"}

        if mode in {"Focus", "Flow"}:
            self.focus_sync_var.set(True)
        else:
            self.focus_sync_var.set(False)

        # Focus/Flow are always focus. Productive can be explicitly promoted
        # when a session genuinely deserves to be part of the focus graph.
        if mode == "Productive":
            self.focus_sync_switch.configure(state="normal")
        else:
            self.focus_sync_switch.configure(state="disabled")
        self._focus_sync_changed()

        if mode == "Productive" and self.category_var.get() in {"", "Break", "Personal"}:
            self.category_var.set("Communication")
        elif mode == "Personal" and self.category_var.get() in {"", "Break"}:
            self.category_var.set("Personal")
        elif mode in {"Short Break", "Long Break"}:
            self.category_var.set("Break")
        elif mode in {"Focus", "Flow"} and self.category_var.get() in {"Break", "Personal"}:
            self.category_var.set("Research")

        if mode in count_up_modes:
            self.state.duration_seconds = 0
            self.state.remaining_seconds = 0
            self.state.elapsed_seconds = 0
            self.time_var.set("00:00")
            self.progress.set(0)
            messages = {
                "Flow": "Flow counts upward and is focused time",
                "Productive": "Productive work counts upward but is not focus by default",
                "Personal": "Personal time is saved locally and excluded from productivity",
            }
            self.timer_status_var.set(messages[mode])
            self._touch_timer("idle")
        else:
            self.set_duration(durations[mode])
            self.state.mode = mode

    def start_pause(self) -> None:
        if not self.state.running:
            if self.state.mode in {"Focus", "Flow", "Productive", "Personal"} and not self.task_var.get().strip():
                defaults = {
                    "Focus": "Focused work",
                    "Flow": "Focused work",
                    "Productive": "Productive task",
                    "Personal": "Personal activity",
                }
                self.task_var.set(defaults[self.state.mode])
            self.state.running = True
            self.state.paused = False
            self.state.session_id = uuid.uuid4().hex
            self.state.started_at = datetime.now(self.app.zone)
            self.state.monotonic_anchor = time.monotonic()
            self.start_button.configure(text="Pause")
            self.timer_status_var.set(f"{self.state.mode} started")
            self._touch_timer("running")
            self._queue_two_way_sync()
            return

        if not self.state.paused:
            self._update_clock()
            self.state.paused = True
            self.start_button.configure(text="Resume")
            self.timer_status_var.set("Paused")
            self._touch_timer("paused")
            self._queue_two_way_sync()
        else:
            self.state.monotonic_anchor = time.monotonic()
            self.state.paused = False
            self.start_button.configure(text="Pause")
            self.timer_status_var.set("Resumed")
            self._touch_timer("running")
            self._queue_two_way_sync()

    def _update_clock(self) -> None:
        if self.state.monotonic_anchor is None:
            return
        delta = int(time.monotonic() - self.state.monotonic_anchor)
        if delta <= 0:
            return
        self.state.monotonic_anchor = time.monotonic()
        self.state.elapsed_seconds += delta
        if self.state.mode in {"Flow", "Productive", "Personal"}:
            self.time_var.set(self.format_seconds(self.state.elapsed_seconds))
            self.progress.set(0)
        else:
            self.state.remaining_seconds = max(0, self.state.remaining_seconds - delta)
            self.time_var.set(self.format_seconds(self.state.remaining_seconds))
            duration = max(1, self.state.duration_seconds)
            self.progress.set(min(1.0, self.state.elapsed_seconds / duration))

    def _publish_monitor_state(self) -> None:
        count_up = self.state.mode in {"Flow", "Productive", "Personal"}
        display_seconds = (
            self.state.elapsed_seconds if count_up else self.state.remaining_seconds
        )
        self.app.publish_monitor_snapshot(
            {
                "session_id": self.state.session_id,
                "revision": self.state.revision,
                "sync_status": self.state.sync_status,
                "source": "desktop",
                "running": bool(self.state.running),
                "paused": bool(self.state.paused),
                "mode": self.state.mode,
                "task": self.task_var.get().strip(),
                "category": self.category_var.get().strip(),
                "counts_toward_focus": bool(self.focus_sync_var.get()),
                "duration_seconds": int(self.state.duration_seconds),
                "elapsed_seconds": int(self.state.elapsed_seconds),
                "remaining_seconds": int(self.state.remaining_seconds),
                "display_seconds": int(display_seconds),
                "status": self.timer_status_var.get(),
                "started_at": self.state.started_at.isoformat() if self.state.started_at else None,
                "updated_at": datetime.now(self.app.zone).isoformat(),
            }
        )

    def tick(self) -> None:
        if self.state.running and not self.state.paused:
            self._update_clock()
            if self.state.mode not in {"Flow", "Productive", "Personal"} and self.state.remaining_seconds <= 0:
                self.finish_timer(natural=True)
        self._publish_monitor_state()
        now_mono = time.monotonic()
        if self.state.running and now_mono - self._last_timer_persist_at >= 5:
            self._persist_timer_state()
            self._last_timer_persist_at = now_mono
        self.after(200, self.tick)

    @staticmethod
    def _human_duration(seconds: int) -> str:
        seconds = max(0, int(seconds))
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        parts: list[str] = []
        if hours:
            parts.append(f"{hours} hour" + ("s" if hours != 1 else ""))
        if minutes:
            parts.append(f"{minutes} minute" + ("s" if minutes != 1 else ""))
        if secs or not parts:
            parts.append(f"{secs} second" + ("s" if secs != 1 else ""))
        return " ".join(parts)

    def activity_summary_text(self) -> str:
        # Capture the most recent elapsed/remaining values before formatting.
        if self.state.running and not self.state.paused:
            self._update_clock()

        now = datetime.now(self.app.zone)
        mode = self.state.mode
        count_up = mode in {"Flow", "Productive", "Personal"}
        task = self.task_var.get().strip() or "Untitled task"
        category = self.category_var.get().strip() or "General"

        if self.state.running:
            status = "Paused" if self.state.paused else "Running"
            started_at = self.state.started_at or now - timedelta(
                seconds=self.state.elapsed_seconds
            )
            start_text = started_at.strftime("%H:%M:%S")
        else:
            status = "Ready / not started"
            start_text = "Not started"

        if count_up:
            estimated_end = "Open-ended (count-up mode)"
            planned_duration = "Open-ended"
            remaining = "Not applicable"
        else:
            planned_seconds = max(0, int(self.state.duration_seconds))
            remaining_seconds = max(0, int(self.state.remaining_seconds))
            planned_duration = self._human_duration(planned_seconds)
            remaining = self._human_duration(remaining_seconds)
            if self.state.running:
                end_at = now + timedelta(seconds=remaining_seconds)
                suffix = " (if resumed now)" if self.state.paused else ""
            else:
                end_at = now + timedelta(seconds=planned_seconds)
                suffix = " (if started now)"
            estimated_end = end_at.strftime("%H:%M:%S") + suffix

        counts_focus = bool(self.focus_sync_var.get())
        sync_text = "Yes, when completed" if counts_focus else "No"
        date_text = now.strftime("%A, %Y-%m-%d")

        return "\n".join(
            [
                "Current activity",
                f"Title: {task}",
                f"Date: {date_text}",
                f"Mode: {mode}",
                f"Category: {category}",
                f"Status: {status}",
                f"Start time: {start_text}",
                f"Estimated end time: {estimated_end}",
                f"Planned duration: {planned_duration}",
                f"Elapsed time: {self._human_duration(self.state.elapsed_seconds)}",
                f"Remaining time: {remaining}",
                f"Counts toward focused time: {'Yes' if counts_focus else 'No'}",
                f"Sync to Pixela: {sync_text}",
                f"Timezone: {self.app.config_data.get('timezone', 'Local time')}",
            ]
        )

    def copy_activity_summary(self) -> None:
        text = self.activity_summary_text()
        try:
            self.app.clipboard_clear()
            self.app.clipboard_append(text)
            self.app.update_idletasks()
        except Exception as exc:
            messagebox.showerror(
                "Clipboard error",
                f"The activity summary could not be copied: {exc}",
            )
            return

        previous = self.timer_status_var.get()
        confirmation = "Activity summary copied to clipboard."
        self.timer_status_var.set(confirmation)

        def restore_status() -> None:
            if self.timer_status_var.get() == confirmation:
                self.timer_status_var.set(previous)

        self.after(2200, restore_status)

    def complete_early(self) -> None:
        if not self.state.running:
            messagebox.showinfo("No active timer", "Start a timer first.")
            return
        self._update_clock()
        min_minutes = max(1, int(self.app.config_data.get("minimum_log_minutes", 1)))
        actual_minutes = max(0, round(self.state.elapsed_seconds / 60))
        if self.state.mode in {"Focus", "Flow", "Productive", "Personal"} and actual_minutes < min_minutes:
            messagebox.showinfo(
                "Session too short",
                f"Complete at least {min_minutes} minute(s) before logging it.",
            )
            return
        self.finish_timer(natural=False)

    def finish_timer(self, natural: bool) -> None:
        self._update_clock()
        mode = self.state.mode
        loggable_modes = {"Focus", "Flow", "Productive", "Personal"}
        should_log = mode in loggable_modes
        counts_toward_focus = bool(self.focus_sync_var.get()) if should_log else False
        actual_minutes = max(1, round(self.state.elapsed_seconds / 60))
        planned_minutes = (
            round(self.state.duration_seconds / 60)
            if self.state.duration_seconds
            else 0
        )

        if should_log:
            ended_at = datetime.now(self.app.zone)
            started_at = self.state.started_at or ended_at - timedelta(
                minutes=actual_minutes
            )
            self.app.store.add_session(
                task=self.task_var.get(),
                category=self.category_var.get(),
                mode=mode,
                counts_toward_focus=counts_toward_focus,
                started_at=started_at,
                ended_at=ended_at,
                planned_minutes=planned_minutes,
                minutes=actual_minutes,
                completed=True,
                sync_id=self.state.session_id or None,
            )

            if mode in {"Focus", "Flow"} and counts_toward_focus:
                self.completed_in_cycle += 1

            if counts_toward_focus:
                self.app.local_status_var.set(
                    f"Saved {actual_minutes} focused minute(s) locally"
                )
                self.app.sync_now()
            elif mode == "Productive":
                self.app.local_status_var.set(
                    f"Saved {actual_minutes} productive minute(s); not sent to Pixela"
                )
            else:
                self.app.local_status_var.set(
                    f"Saved {actual_minutes} personal minute(s); not sent to Pixela"
                )

        self.state.running = False
        self.state.paused = False
        self.state.monotonic_anchor = None
        self._touch_timer("completed")
        self._queue_two_way_sync()

        if self.app.config_data.get("sound_enabled", True):
            self.bell()

        self.state.running = False
        self.state.paused = False
        self.start_button.configure(text="Start")

        if mode in {"Focus", "Flow"}:
            cycle = max(1, int(self.app.config_data["sessions_before_long_break"]))
            next_mode = (
                "Long Break"
                if self.completed_in_cycle % cycle == 0
                else "Short Break"
            )
            self.mode_var.set(next_mode)
            self.state.mode = next_mode
            self.change_mode(next_mode)
            self.timer_status_var.set(
                f"Session saved. Next: {next_mode.lower()}."
            )
            if self.app.config_data.get("auto_start_breaks", False):
                self.after(500, self.start_pause)
        elif mode in {"Productive", "Personal"}:
            self.task_var.set("")
            self.category_var.set("Research")
            self.mode_var.set("Focus")
            self.state.mode = "Focus"
            self.change_mode("Focus")
            self.timer_status_var.set(
                f"{mode} session saved. Ready for a focus block."
            )
        else:
            self.mode_var.set("Focus")
            self.state.mode = "Focus"
            self.change_mode("Focus")
            self.timer_status_var.set(
                "Break complete. Ready for the next focus block."
            )
            if self.app.config_data.get("auto_start_focus", False):
                self.after(500, self.start_pause)

        self.refresh()
        self.app.refresh_data_views()

    def reset_timer(self) -> None:
        was_active = self.state.running
        self.state.running = False
        self.state.paused = False
        self.state.started_at = None
        self.state.monotonic_anchor = None
        self.state.elapsed_seconds = 0
        if self.state.mode in {"Flow", "Productive", "Personal"}:
            self.state.remaining_seconds = 0
        else:
            self.state.remaining_seconds = self.state.duration_seconds
        self.time_var.set(self.format_seconds(self.state.remaining_seconds))
        self.progress.set(0)
        self.start_button.configure(text="Start")
        self.timer_status_var.set("Reset")
        self._touch_timer("canceled" if was_active else "idle")
        if was_active:
            self._queue_two_way_sync()


class SchedulePage(PageBase):
    COLUMNS = ("start", "end", "title", "category", "notes")

    def __init__(self, master, app: FocusApp):
        super().__init__(
            master,
            app,
            "Schedule planner",
            "Add only the events you actually plan. Empty days stay empty.",
        )
        self.selected_date = datetime.now(app.zone).date()
        self.week_anchor = self.selected_date - timedelta(
            days=self.selected_date.weekday()
        )
        self.date_var = ctk.StringVar(value=self.selected_date.isoformat())
        self.day_title_var = ctk.StringVar()
        self.count_var = ctk.StringVar()
        self.day_buttons: list[ctk.CTkButton] = []
        self._build()
        self.refresh()

    def _build(self) -> None:
        body = ctk.CTkFrame(self, corner_radius=16)
        body.grid(row=1, column=0, padx=26, pady=(0, 24), sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(3, weight=1)

        toolbar = ctk.CTkFrame(body, fg_color="transparent")
        toolbar.grid(row=0, column=0, padx=16, pady=(16, 8), sticky="ew")
        toolbar.grid_columnconfigure(4, weight=1)

        ctk.CTkButton(
            toolbar,
            text="← Previous week",
            width=116,
            command=lambda: self.change_week(-7),
        ).grid(row=0, column=0, padx=(0, 6))
        ctk.CTkButton(
            toolbar,
            text="Today",
            width=76,
            fg_color=("#e2e8f0", "#2b3542"),
            text_color=("#1f2937", "#e5e7eb"),
            hover_color=("#cbd5e1", "#3b4756"),
            command=self.go_today,
        ).grid(row=0, column=1, padx=3)
        ctk.CTkButton(
            toolbar,
            text="Next week →",
            width=106,
            command=lambda: self.change_week(7),
        ).grid(row=0, column=2, padx=3)

        date_entry = ctk.CTkEntry(
            toolbar,
            width=132,
            textvariable=self.date_var,
            placeholder_text="YYYY-MM-DD",
        )
        date_entry.grid(row=0, column=3, padx=(14, 5))
        date_entry.bind("<Return>", lambda _event: self.go_to_date())
        ctk.CTkButton(
            toolbar,
            text="Go",
            width=50,
            fg_color=("#e2e8f0", "#2b3542"),
            text_color=("#1f2937", "#e5e7eb"),
            hover_color=("#cbd5e1", "#3b4756"),
            command=self.go_to_date,
        ).grid(row=0, column=4, sticky="w")

        ctk.CTkButton(
            toolbar,
            text="+ Add event",
            width=108,
            command=self.add_event,
        ).grid(row=0, column=5, padx=(8, 0))

        strip = ctk.CTkFrame(body, fg_color="transparent")
        strip.grid(row=1, column=0, padx=16, pady=(0, 8), sticky="ew")
        strip.grid_columnconfigure(tuple(range(7)), weight=1)
        for column in range(7):
            button = ctk.CTkButton(
                strip,
                text="",
                height=58,
                corner_radius=10,
                command=lambda index=column: self.select_week_day(index),
            )
            button.grid(row=0, column=column, padx=3, sticky="ew")
            self.day_buttons.append(button)

        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.grid(row=2, column=0, padx=16, pady=(2, 8), sticky="ew")
        ctk.CTkLabel(
            actions,
            textvariable=self.day_title_var,
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(side="left")
        ctk.CTkLabel(
            actions,
            textvariable=self.count_var,
            text_color=("#64748b", "#94a3b8"),
        ).pack(side="left", padx=12)

        for text, command in (
            ("Edit", self.edit_selected),
            ("Duplicate", self.duplicate_selected),
            ("Delete", self.delete_selected),
            ("Export CSV", self.export_csv),
        ):
            ctk.CTkButton(
                actions,
                text=text,
                height=31,
                width=88,
                fg_color=("#e2e8f0", "#2b3542"),
                text_color=("#1f2937", "#e5e7eb"),
                hover_color=("#cbd5e1", "#3b4756"),
                command=command,
            ).pack(side="right", padx=(6, 0))

        table_host = ctk.CTkFrame(body, corner_radius=10)
        table_host.grid(row=3, column=0, padx=16, pady=(0, 8), sticky="nsew")
        table_host.grid_columnconfigure(0, weight=1)
        table_host.grid_rowconfigure(0, weight=1)

        SessionsPage._configure_tree_style()
        self.tree = ttk.Treeview(
            table_host,
            columns=self.COLUMNS,
            show="headings",
            selectmode="extended",
        )
        setup = (
            ("start", "Start", 78, "center"),
            ("end", "End", 78, "center"),
            ("title", "Event", 310, "w"),
            ("category", "Category", 150, "w"),
            ("notes", "Notes", 330, "w"),
        )
        for key, title, width, anchor in setup:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, anchor=anchor)

        y_scroll = ttk.Scrollbar(
            table_host,
            orient="vertical",
            command=self.tree.yview,
        )
        x_scroll = ttk.Scrollbar(
            table_host,
            orient="horizontal",
            command=self.tree.xview,
        )
        self.tree.configure(
            yscrollcommand=y_scroll.set,
            xscrollcommand=x_scroll.set,
        )
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<Double-1>", lambda _event: self.edit_selected())
        self.tree.bind("<Delete>", lambda _event: self.delete_selected())

        self.empty_hint = ctk.CTkLabel(
            body,
            text="No events are created automatically. Use “Add event” when you need one.",
            text_color=("#64748b", "#94a3b8"),
            font=ctk.CTkFont(size=12),
        )
        self.empty_hint.grid(row=4, column=0, padx=16, pady=(0, 14), sticky="w")

    def _on_return(self, event) -> None:
        widget = self.focus_get()
        if widget is not None and widget.winfo_class() in {"Entry", "TEntry"}:
            self.go_to_date()

    def go_today(self) -> None:
        self.selected_date = datetime.now(self.app.zone).date()
        self.week_anchor = self.selected_date - timedelta(
            days=self.selected_date.weekday()
        )
        self.date_var.set(self.selected_date.isoformat())
        self.refresh()

    def go_to_date(self) -> None:
        try:
            selected = datetime.fromisoformat(
                self.date_var.get().strip()
            ).date()
        except ValueError:
            messagebox.showerror(
                "Invalid date",
                "Use YYYY-MM-DD, for example 2026-07-06.",
            )
            return
        self.selected_date = selected
        self.week_anchor = selected - timedelta(days=selected.weekday())
        self.date_var.set(selected.isoformat())
        self.refresh()

    def change_week(self, days: int) -> None:
        self.week_anchor += timedelta(days=days)
        self.selected_date = self.week_anchor
        self.date_var.set(self.selected_date.isoformat())
        self.refresh()

    def select_week_day(self, index: int) -> None:
        self.selected_date = self.week_anchor + timedelta(days=index)
        self.date_var.set(self.selected_date.isoformat())
        self.refresh()

    def refresh(self) -> None:
        today = datetime.now(self.app.zone).date()
        for index, button in enumerate(self.day_buttons):
            day = self.week_anchor + timedelta(days=index)
            count = len(self.app.schedule_store.events_on(day))
            suffix = f"\n{count} event" if count == 1 else (
                f"\n{count} events" if count else "\n—"
            )
            button.configure(
                text=f"{day:%a}\n{day:%b %d}{suffix}",
                fg_color=(
                    ("#bfdbfe", "#1e3a5f")
                    if day == self.selected_date
                    else ("#e2e8f0", "#28313d")
                ),
                text_color=(
                    ("#1d4ed8", "#bfdbfe")
                    if day == self.selected_date
                    else ("#1f2937", "#e5e7eb")
                ),
                border_width=1 if day == today else 0,
                border_color=("#2563eb", "#60a5fa"),
            )

        rows = self.app.schedule_store.events_on(self.selected_date)
        for item in self.tree.get_children():
            self.tree.delete(item)
        for row in rows:
            self.tree.insert(
                "",
                "end",
                iid=row["id"],
                values=(
                    row["start"],
                    row["end"],
                    row["title"],
                    row["category"],
                    row["notes"],
                ),
            )

        self.day_title_var.set(
            self.selected_date.strftime("%A, %B %d, %Y")
        )
        self.count_var.set(
            "No events"
            if not rows
            else f"{len(rows)} event{'s' if len(rows) != 1 else ''}"
        )
        self.empty_hint.configure(
            text=(
                "No events for this day. Use “Add event” to create one."
                if not rows
                else "Double-click an event to edit it. Delete removes only the selected event."
            )
        )

    def selected_ids(self) -> list[str]:
        return list(self.tree.selection())

    def add_event(self) -> None:
        ScheduleEventDialog(
            self,
            self.app,
            event_date=self.selected_date,
            on_saved=self._after_change,
        )

    def edit_selected(self) -> None:
        ids = self.selected_ids()
        if len(ids) != 1:
            messagebox.showinfo(
                "Select one event",
                "Select exactly one event to edit.",
            )
            return
        event = self.app.schedule_store.get(ids[0])
        if event is None:
            return
        ScheduleEventDialog(
            self,
            self.app,
            event=event,
            event_date=self.selected_date,
            on_saved=self._after_change,
        )

    def duplicate_selected(self) -> None:
        ids = self.selected_ids()
        if len(ids) != 1:
            messagebox.showinfo(
                "Select one event",
                "Select exactly one event to duplicate.",
            )
            return
        event = self.app.schedule_store.get(ids[0])
        if event is None:
            return
        duplicate = dict(event)
        duplicate.pop("id", None)
        duplicate["title"] = f"{duplicate['title']} (copy)"
        ScheduleEventDialog(
            self,
            self.app,
            event=duplicate,
            event_date=self.selected_date,
            on_saved=self._after_change,
            force_add=True,
        )

    def delete_selected(self) -> None:
        ids = self.selected_ids()
        if not ids:
            messagebox.showinfo(
                "Select events",
                "Select one or more events first.",
            )
            return
        if not messagebox.askyesno(
            "Delete events",
            f"Delete {len(ids)} selected event(s)?",
        ):
            return
        changed = self.app.schedule_store.delete(ids)
        self.app.local_status_var.set(f"Deleted {changed} schedule event(s)")
        self._after_change()

    def export_csv(self) -> None:
        path_text = filedialog.asksaveasfilename(
            title="Export schedule",
            initialdir=EXPORT_DIR,
            initialfile=f"schedule_{datetime.now():%Y%m%d_%H%M}.csv",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
        )
        if not path_text:
            return
        count = self.app.schedule_store.export(Path(path_text))
        messagebox.showinfo(
            "Schedule exported",
            f"Exported {count} event(s).",
        )

    def _after_change(self) -> None:
        self.refresh()
        self.app.publish_cloud_snapshot_now()
        if self.app.cloud_syncer is not None:
            self.app.cloud_syncer.submit(self.app._cloud_sync_payload())


class ScheduleEventDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master,
        app: FocusApp,
        *,
        event_date,
        on_saved,
        event: dict[str, str] | None = None,
        force_add: bool = False,
    ):
        super().__init__(master)
        self.app = app
        self.event = event
        self.on_saved = on_saved
        self.force_add = force_add
        self.title("Edit schedule event" if event and not force_add else "Add schedule event")
        self.geometry("560x590")
        self.minsize(520, 540)
        self.transient(master)
        self.grab_set()

        values = event or {}
        self.date_var = ctk.StringVar(
            value=str(values.get("date") or event_date.isoformat())
        )
        self.start_var = ctk.StringVar(value=str(values.get("start") or "09:00"))
        self.end_var = ctk.StringVar(value=str(values.get("end") or "10:00"))
        self.title_var = ctk.StringVar(value=str(values.get("title") or ""))
        self.category_var = ctk.StringVar(
            value=str(values.get("category") or "General")
        )

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="both", expand=True, padx=24, pady=20)
        form.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkLabel(form, text="Date").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(form, text="Category").grid(
            row=0, column=1, padx=(12, 0), sticky="w"
        )
        ctk.CTkEntry(
            form,
            textvariable=self.date_var,
            placeholder_text="YYYY-MM-DD",
        ).grid(row=1, column=0, pady=(4, 12), sticky="ew")
        categories = sorted(
            set(app.config_data.get("categories", []))
            | set(app.schedule_store.distinct_categories())
            | {"General", "Research", "Coding", "Communication", "Admin", "Personal"}
        )
        ctk.CTkComboBox(
            form,
            variable=self.category_var,
            values=categories,
        ).grid(row=1, column=1, padx=(12, 0), pady=(4, 12), sticky="ew")

        ctk.CTkLabel(form, text="Start").grid(row=2, column=0, sticky="w")
        ctk.CTkLabel(form, text="End").grid(
            row=2, column=1, padx=(12, 0), sticky="w"
        )
        ctk.CTkEntry(
            form,
            textvariable=self.start_var,
            placeholder_text="09:00",
        ).grid(row=3, column=0, pady=(4, 12), sticky="ew")
        ctk.CTkEntry(
            form,
            textvariable=self.end_var,
            placeholder_text="10:00",
        ).grid(row=3, column=1, padx=(12, 0), pady=(4, 12), sticky="ew")

        ctk.CTkLabel(form, text="Event title").grid(
            row=4, column=0, columnspan=2, sticky="w"
        )
        title_entry = ctk.CTkEntry(
            form,
            textvariable=self.title_var,
            placeholder_text="Example: Research meeting",
        )
        title_entry.grid(
            row=5, column=0, columnspan=2, pady=(4, 12), sticky="ew"
        )

        ctk.CTkLabel(form, text="Notes (optional)").grid(
            row=6, column=0, columnspan=2, sticky="w"
        )
        self.notes = ctk.CTkTextbox(form, height=170)
        self.notes.grid(
            row=7, column=0, columnspan=2, pady=(4, 14), sticky="nsew"
        )
        self.notes.insert("1.0", str(values.get("notes") or ""))
        form.grid_rowconfigure(7, weight=1)

        buttons = ctk.CTkFrame(form, fg_color="transparent")
        buttons.grid(row=8, column=0, columnspan=2, sticky="ew")
        buttons.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(
            buttons,
            text="Cancel",
            fg_color=("#e2e8f0", "#2b3542"),
            text_color=("#1f2937", "#e5e7eb"),
            hover_color=("#cbd5e1", "#3b4756"),
            command=self.destroy,
        ).grid(row=0, column=0, padx=(0, 6), sticky="ew")
        ctk.CTkButton(
            buttons,
            text="Save event",
            command=self.save,
        ).grid(row=0, column=1, padx=(6, 0), sticky="ew")

        self.after(120, title_entry.focus_set)

    def save(self) -> None:
        values = {
            "event_date": self.date_var.get(),
            "start": self.start_var.get(),
            "end": self.end_var.get(),
            "title": self.title_var.get(),
            "category": self.category_var.get(),
            "notes": self.notes.get("1.0", "end").strip(),
        }
        try:
            if self.event and not self.force_add and self.event.get("id"):
                self.app.schedule_store.update(
                    self.event["id"],
                    **values,
                )
            else:
                self.app.schedule_store.add(**values)
        except ScheduleValidationError as exc:
            messagebox.showerror(
                "Cannot save event",
                str(exc),
                parent=self,
            )
            return

        self.app.local_status_var.set("Schedule saved")
        self.on_saved()
        self.destroy()

class SessionsPage(PageBase):
    COLUMNS = ("id", "date", "start", "task", "category", "mode", "planned", "actual", "focus", "sync")

    def __init__(self, master, app: FocusApp):
        super().__init__(
            master,
            app,
            "Session log",
            "Searchable, copyable records saved in SQLite and mirrored to readable CSV files.",
        )
        self.search_var = ctk.StringVar(value="")
        self.category_var = ctk.StringVar(value="All")
        self.range_var = ctk.StringVar(value="30 days")
        self.count_var = ctk.StringVar(value="")
        self.page_var = ctk.StringVar(value="Page 1 of 1")
        self.page_size = 100
        self.page = 1
        self.total_rows = 0
        self._build()
        self.refresh()

    def _build(self) -> None:
        body = ctk.CTkFrame(self, corner_radius=16)
        body.grid(row=1, column=0, padx=26, pady=(0, 24), sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(2, weight=1)

        filters = ctk.CTkFrame(body, fg_color="transparent")
        filters.grid(row=0, column=0, padx=16, pady=(16, 8), sticky="ew")
        filters.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(
            filters,
            placeholder_text="Search tasks, categories, or notes",
            textvariable=self.search_var,
        ).grid(row=0, column=0, padx=(0, 8), sticky="ew")
        self.category_filter = ctk.CTkComboBox(
            filters,
            variable=self.category_var,
            values=["All"],
            width=150,
            command=lambda _value: self.apply_filters(),
        )
        self.category_filter.grid(row=0, column=1, padx=4)
        ctk.CTkComboBox(
            filters,
            variable=self.range_var,
            values=["1 day", "3 days", "7 days", "30 days", "90 days", "All"],
            width=120,
            command=lambda _value: self.apply_filters(),
        ).grid(row=0, column=2, padx=4)
        ctk.CTkButton(filters, text="Search", width=82, command=self.apply_filters).grid(row=0, column=3, padx=(8, 0))

        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.grid(row=1, column=0, padx=16, pady=(0, 8), sticky="ew")
        for text, command in (
            ("Copy selected", self.copy_selected),
            ("Edit", self.edit_selected),
            ("Delete", self.delete_selected),
            ("Import CSV", self.import_csv),
            ("Export CSV", self.export_csv),
            ("Backup DB", self.backup_db),
        ):
            ctk.CTkButton(
                actions,
                text=text,
                height=32,
                width=100,
                fg_color=("#e2e8f0", "#2b3542"),
                text_color=("#1f2937", "#e5e7eb"),
                hover_color=("#cbd5e1", "#3b4756"),
                command=command,
            ).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(actions, textvariable=self.count_var).pack(side="right")

        table_host = ctk.CTkFrame(body, corner_radius=10)
        table_host.grid(row=2, column=0, padx=16, pady=(0, 16), sticky="nsew")
        table_host.grid_columnconfigure(0, weight=1)
        table_host.grid_rowconfigure(0, weight=1)

        self._configure_tree_style()
        self.tree = ttk.Treeview(table_host, columns=self.COLUMNS, show="headings", selectmode="extended")
        headings = {
            "id": "ID",
            "date": "Date",
            "start": "Start",
            "task": "Task",
            "category": "Category",
            "mode": "Mode",
            "planned": "Planned",
            "actual": "Actual",
            "focus": "Focus?",
            "sync": "Pixela",
        }
        widths = {"id": 50, "date": 100, "start": 70, "task": 250, "category": 125, "mode": 95, "planned": 75, "actual": 75, "focus": 70, "sync": 85}
        for key in self.COLUMNS:
            self.tree.heading(key, text=headings[key])
            self.tree.column(key, width=widths[key], anchor="w" if key in {"task", "category"} else "center")

        y_scroll = ttk.Scrollbar(table_host, orient="vertical", command=self.tree.yview)
        x_scroll = ttk.Scrollbar(table_host, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<Control-c>", lambda _event: self.copy_selected())
        self.tree.bind("<Double-1>", lambda _event: self.edit_selected())

        pagination = ctk.CTkFrame(body, fg_color="transparent")
        pagination.grid(row=3, column=0, padx=16, pady=(0, 16), sticky="ew")
        pagination.grid_columnconfigure(2, weight=1)
        self.first_page_button = ctk.CTkButton(
            pagination, text="⏮ First", width=82, command=lambda: self.go_to_page(1)
        )
        self.first_page_button.grid(row=0, column=0, padx=(0, 5))
        self.previous_page_button = ctk.CTkButton(
            pagination, text="◀ Previous", width=96, command=lambda: self.go_to_page(self.page - 1)
        )
        self.previous_page_button.grid(row=0, column=1, padx=5)
        ctk.CTkLabel(pagination, textvariable=self.page_var).grid(
            row=0, column=2, padx=12, sticky="ew"
        )
        self.next_page_button = ctk.CTkButton(
            pagination, text="Next ▶", width=82, command=lambda: self.go_to_page(self.page + 1)
        )
        self.next_page_button.grid(row=0, column=3, padx=5)
        self.last_page_button = ctk.CTkButton(
            pagination, text="Last ⏭", width=82, command=self.go_to_last_page
        )
        self.last_page_button.grid(row=0, column=4, padx=(5, 0))

    @staticmethod
    def _configure_tree_style() -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        mode = ctk.get_appearance_mode().lower()
        if mode == "dark":
            background, foreground, field, selected = "#1f2937", "#e5e7eb", "#1f2937", "#1d4ed8"
        else:
            background, foreground, field, selected = "#ffffff", "#1f2937", "#ffffff", "#bfdbfe"
        style.configure("Treeview", background=background, fieldbackground=field, foreground=foreground, rowheight=30, borderwidth=0)
        style.configure("Treeview.Heading", relief="flat", padding=7)
        style.map("Treeview", background=[("selected", selected)])

    def _range_days(self) -> int | None:
        return {"1 day": 1, "3 days": 3, "7 days": 7, "30 days": 30, "90 days": 90, "All": None}.get(self.range_var.get())

    def apply_filters(self) -> None:
        self.page = 1
        self.refresh()

    def _page_count(self) -> int:
        return max(1, (self.total_rows + self.page_size - 1) // self.page_size)

    def go_to_page(self, page: int) -> None:
        target = max(1, min(int(page), self._page_count()))
        if target == self.page:
            return
        self.page = target
        self.refresh()

    def go_to_last_page(self) -> None:
        self.go_to_page(self._page_count())

    def refresh(self) -> None:
        categories = ["All"] + sorted(set(self.app.config_data.get("categories", [])) | set(self.app.store.distinct_categories()))
        self.category_filter.configure(values=categories)
        if self.category_var.get() not in categories:
            self.category_var.set("All")

        query = {
            "search": self.search_var.get(),
            "category": self.category_var.get(),
            "days": self._range_days(),
        }
        self.total_rows = self.app.store.count_sessions(**query)
        pages = self._page_count()
        self.page = max(1, min(self.page, pages))
        rows = self.app.store.list_sessions(
            **query,
            limit=self.page_size,
            offset=(self.page - 1) * self.page_size,
        )
        for item in self.tree.get_children():
            self.tree.delete(item)
        page_minutes = 0
        for row in rows:
            started = datetime.fromisoformat(row["started_at_utc"]).astimezone(self.app.zone)
            page_minutes += int(row["minutes"])
            self.tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(
                    row["id"],
                    started.strftime("%Y-%m-%d"),
                    started.strftime("%H:%M"),
                    row["task"],
                    row["category"],
                    row["mode"],
                    f"{row['planned_minutes']}m",
                    f"{row['minutes']}m",
                    "Yes" if row["counts_toward_focus"] else "No",
                    (
                        "Synced" if row["synced"] else "Pending"
                    ) if row["counts_toward_focus"] else "Not sent",
                ),
            )
        start_number = 0 if not rows else (self.page - 1) * self.page_size + 1
        end_number = (self.page - 1) * self.page_size + len(rows)
        self.count_var.set(
            f"{self.total_rows} sessions · showing {start_number}-{end_number} · {page_minutes} min on page"
        )
        self.page_var.set(f"Page {self.page} of {pages}")
        first_state = "disabled" if self.page <= 1 else "normal"
        last_state = "disabled" if self.page >= pages else "normal"
        self.first_page_button.configure(state=first_state)
        self.previous_page_button.configure(state=first_state)
        self.next_page_button.configure(state=last_state)
        self.last_page_button.configure(state=last_state)

    def selected_ids(self) -> list[int]:
        return [int(item) for item in self.tree.selection()]

    def copy_selected(self) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        header = [self.tree.heading(column)["text"] for column in self.COLUMNS]
        lines = ["\t".join(header)]
        for item in selected:
            lines.append("\t".join(str(value) for value in self.tree.item(item, "values")))
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self.app.local_status_var.set(f"Copied {len(selected)} session row(s)")

    def delete_selected(self) -> None:
        ids = self.selected_ids()
        if not ids:
            messagebox.showinfo("Select sessions", "Select one or more rows first.")
            return
        if not messagebox.askyesno("Delete sessions", f"Delete {len(ids)} selected session(s)?"):
            return
        changed = self.app.store.delete_sessions(ids)
        self.app.local_status_var.set(f"Deleted {changed} session(s)")
        self.refresh()
        self.app.refresh_data_views()

    def edit_selected(self) -> None:
        ids = self.selected_ids()
        if len(ids) != 1:
            messagebox.showinfo("Select one session", "Select exactly one row to edit.")
            return
        row = self.app.store.get_session(ids[0])
        if row is None:
            return
        SessionEditDialog(self, self.app, row, on_saved=self.refresh)

    def import_csv(self) -> None:
        path_text = filedialog.askopenfilename(
            title="Import focus sessions",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path_text:
            return
        try:
            rows = read_session_csv(Path(path_text), self.app.config_data["timezone"])
            inserted, skipped = self.app.store.import_sessions(rows)
        except CsvImportError as exc:
            messagebox.showerror("CSV import failed", str(exc))
            return
        messagebox.showinfo("CSV import complete", f"Inserted: {inserted}\nSkipped duplicates: {skipped}")
        self.app.store.mark_all_unsynced()
        self.app.refresh_data_views()

    def export_csv(self) -> None:
        default_name = f"focus_sessions_{datetime.now():%Y%m%d_%H%M}.csv"
        path_text = filedialog.asksaveasfilename(
            title="Export focus sessions",
            initialdir=EXPORT_DIR,
            initialfile=default_name,
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
        )
        if not path_text:
            return
        rows = self.app.store.list_sessions(
            search=self.search_var.get(),
            category=self.category_var.get(),
            days=self._range_days(),
            limit=1_000_000,
        )
        count = self.app.store.export_csv(Path(path_text), rows)
        messagebox.showinfo("Export complete", f"Exported {count} session(s).")

    def backup_db(self) -> None:
        default_name = f"focus_history_backup_{datetime.now():%Y%m%d_%H%M}.db"
        path_text = filedialog.asksaveasfilename(
            title="Backup SQLite database",
            initialdir=EXPORT_DIR,
            initialfile=default_name,
            defaultextension=".db",
            filetypes=[("SQLite database", "*.db")],
        )
        if not path_text:
            return
        self.app.store.backup_database(Path(path_text))
        messagebox.showinfo("Backup complete", "The SQLite database was copied successfully.")


class SessionEditDialog(ctk.CTkToplevel):
    def __init__(self, master, app: FocusApp, row, on_saved):
        super().__init__(master)
        self.app = app
        self.row = row
        self.on_saved = on_saved
        self.title(f"Edit session #{row['id']}")
        self.geometry("520x500")
        self.transient(master)
        self.grab_set()

        self.task_var = ctk.StringVar(value=row["task"])
        self.category_var = ctk.StringVar(value=row["category"])
        self.mode_var = ctk.StringVar(value=row["mode"])
        self.focus_var = ctk.BooleanVar(value=bool(row["counts_toward_focus"]))
        self.minutes_var = ctk.StringVar(value=str(row["minutes"]))

        ctk.CTkLabel(self, text="Task").pack(anchor="w", padx=24, pady=(22, 4))
        ctk.CTkEntry(self, textvariable=self.task_var).pack(fill="x", padx=24)
        ctk.CTkLabel(self, text="Category").pack(anchor="w", padx=24, pady=(14, 4))
        ctk.CTkEntry(self, textvariable=self.category_var).pack(fill="x", padx=24)
        ctk.CTkLabel(self, text="Activity type").pack(anchor="w", padx=24, pady=(14, 4))
        ctk.CTkComboBox(
            self,
            variable=self.mode_var,
            values=["Focus", "Flow", "Productive", "Personal"],
        ).pack(fill="x", padx=24)
        ctk.CTkSwitch(
            self,
            text="Count toward focused time and sync to Pixela",
            variable=self.focus_var,
        ).pack(anchor="w", padx=24, pady=(14, 0))
        ctk.CTkLabel(self, text="Actual minutes").pack(anchor="w", padx=24, pady=(14, 4))
        ctk.CTkEntry(self, textvariable=self.minutes_var).pack(fill="x", padx=24)
        ctk.CTkLabel(self, text="Notes").pack(anchor="w", padx=24, pady=(14, 4))
        self.notes = ctk.CTkTextbox(self, height=90)
        self.notes.pack(fill="both", expand=True, padx=24)
        self.notes.insert("1.0", row["notes"])
        ctk.CTkButton(self, text="Save changes", command=self.save).pack(pady=18)

    def save(self) -> None:
        try:
            minutes = int(self.minutes_var.get())
        except ValueError:
            messagebox.showerror("Invalid minutes", "Minutes must be a whole number.", parent=self)
            return
        self.app.store.update_session(
            int(self.row["id"]),
            task=self.task_var.get(),
            category=self.category_var.get(),
            mode=self.mode_var.get(),
            counts_toward_focus=bool(self.focus_var.get()),
            notes=self.notes.get("1.0", "end").strip(),
            minutes=minutes,
        )
        self.app.refresh_data_views()
        self.on_saved()
        self.destroy()


class AnalyticsPage(PageBase):
    def __init__(self, master, app: FocusApp):
        super().__init__(
            master,
            app,
            "Analytics",
            "Use trends to adjust your system—not to judge individual days.",
        )
        self.period_var = ctk.StringVar(value="30 days")
        self.total_var = ctk.StringVar()
        self.other_productive_var = ctk.StringVar()
        self.total_productive_var = ctk.StringVar()
        self.sessions_var = ctk.StringVar()
        self.streak_var = ctk.StringVar()
        self._build()
        self.refresh()

    def _build(self) -> None:
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, padx=26, pady=(0, 24), sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(2, weight=1)

        period = ctk.CTkSegmentedButton(
            body,
            values=["1 day", "3 days", "7 days", "30 days", "90 days", "All"],
            variable=self.period_var,
            command=lambda _value: self.refresh(),
        )
        period.grid(row=0, column=0, sticky="w", pady=(0, 12))

        cards = ctk.CTkFrame(body, fg_color="transparent")
        cards.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        cards.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)
        for column, (title, variable) in enumerate(
            [
                ("Focused time", self.total_var),
                ("Other productive", self.other_productive_var),
                ("Total productive", self.total_productive_var),
                ("Productive sessions", self.sessions_var),
                ("Focus streak", self.streak_var),
            ]
        ):
            card = ctk.CTkFrame(cards, corner_radius=14)
            card.grid(row=0, column=column, padx=(0 if column == 0 else 4, 0 if column == 4 else 4), sticky="ew")
            ctk.CTkLabel(card, text=title, text_color=("#64748b", "#94a3b8")).pack(anchor="w", padx=16, pady=(14, 2))
            ctk.CTkLabel(card, textvariable=variable, font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w", padx=16, pady=(0, 14))

        self.chart = AnalyticsChart(body)
        self.chart.grid(row=2, column=0, sticky="nsew")

    def _days(self) -> int | None:
        return {"1 day": 1, "3 days": 3, "7 days": 7, "30 days": 30, "90 days": 90, "All": None}[self.period_var.get()]

    def refresh(self) -> None:
        days = self._days()
        summary = self.app.store.analytics_summary(days)
        focus_total = int(summary["focus_minutes"])
        other_total = int(summary["other_productive_minutes"])
        productive_total = int(summary["total_productive_minutes"])
        self.total_var.set(f"{focus_total / 60:.1f} h\n{focus_total} min")
        self.other_productive_var.set(
            f"{other_total / 60:.1f} h\n{other_total} min"
        )
        self.total_productive_var.set(
            f"{productive_total / 60:.1f} h\n{productive_total} min"
        )
        self.sessions_var.set(str(summary["total_productive_sessions"]))
        self.streak_var.set(f"{summary['streak']} day(s)")
        appearance = ctk.get_appearance_mode()
        self.chart.draw(
            self.app.store.daily_totals(days, "focus"),
            self.app.store.daily_totals(days, "other_productive"),
            self.app.store.category_totals(days, "productive"),
            self.app.store.weekday_totals(days, "focus"),
            self.app.store.weekday_totals(days, "other_productive"),
            appearance,
        )


class PixelaPage(PageBase):
    def __init__(self, master, app: FocusApp):
        super().__init__(
            master,
            app,
            "Pixela connection",
            "Connection diagnostics, pending data, sync history, and the official Pixela SVG preview.",
        )
        self.connection_var = ctk.StringVar(value="Not checked")
        self.last_sync_var = ctk.StringVar(value="Never")
        self.pending_var = ctk.StringVar(value="0")
        self.preview_status_var = ctk.StringVar(value="Press Refresh preview")
        self.preview_image = None
        self._build()
        self.refresh()

    def _build(self) -> None:
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, padx=26, pady=(0, 24), sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)

        preview_card = ctk.CTkFrame(body, corner_radius=16)
        preview_card.grid(row=0, column=0, pady=(0, 12), sticky="ew")
        preview_card.grid_columnconfigure(0, weight=1)

        preview_header = ctk.CTkFrame(preview_card, fg_color="transparent")
        preview_header.grid(row=0, column=0, padx=16, pady=(14, 6), sticky="ew")
        preview_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            preview_header,
            text="Full Pixela activity graph",
            font=ctk.CTkFont(size=17, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            preview_header,
            text="Full calendar view — no compact 90-day mode",
            text_color=("#64748b", "#94a3b8"),
        ).grid(row=1, column=0, sticky="w")
        ctk.CTkButton(
            preview_header,
            text="Refresh graph",
            width=104,
            command=self.refresh_preview,
        ).grid(row=0, column=1, rowspan=2, padx=(8, 0))
        ctk.CTkButton(
            preview_header,
            text="Open Pixela",
            width=104,
            command=self.open_graph,
        ).grid(row=0, column=2, rowspan=2, padx=(6, 0))

        graph_host = ctk.CTkScrollableFrame(
            preview_card,
            orientation="horizontal",
            height=230,
            corner_radius=10,
        )
        graph_host.grid(row=1, column=0, padx=16, pady=(4, 8), sticky="ew")
        self.preview_label = ctk.CTkLabel(
            graph_host,
            textvariable=self.preview_status_var,
            width=1060,
            height=205,
        )
        self.preview_label.pack(anchor="w", padx=6, pady=6)

        ctk.CTkLabel(
            preview_card,
            textvariable=self.preview_status_var,
            text_color=("#64748b", "#94a3b8"),
            font=ctk.CTkFont(size=11),
        ).grid(row=2, column=0, padx=16, pady=(0, 12), sticky="w")

        lower = ctk.CTkFrame(body, fg_color="transparent")
        lower.grid(row=1, column=0, sticky="nsew")
        lower.grid_columnconfigure(0, weight=2)
        lower.grid_columnconfigure(1, weight=3)
        lower.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(lower, corner_radius=16)
        left.grid(row=0, column=0, padx=(0, 10), sticky="nsew")
        left.grid_columnconfigure(0, weight=1)

        cards = ctk.CTkFrame(left, fg_color="transparent")
        cards.grid(row=0, column=0, padx=16, pady=(16, 8), sticky="ew")
        cards.grid_columnconfigure(0, weight=1)
        for row_index, (title, var) in enumerate(
            [
                ("Connection", self.connection_var),
                ("Last sync", self.last_sync_var),
                ("Pending days", self.pending_var),
            ]
        ):
            card = ctk.CTkFrame(cards, corner_radius=12)
            card.grid(
                row=row_index,
                column=0,
                pady=(0, 7),
                sticky="ew",
            )
            ctk.CTkLabel(
                card,
                text=title,
                text_color=("#64748b", "#94a3b8"),
            ).pack(anchor="w", padx=12, pady=(10, 2))
            ctk.CTkLabel(
                card,
                textvariable=var,
                wraplength=330,
                justify="left",
                font=ctk.CTkFont(size=14, weight="bold"),
            ).pack(anchor="w", padx=12, pady=(0, 10))

        actions = ctk.CTkFrame(left, fg_color="transparent")
        actions.grid(row=1, column=0, padx=16, pady=(4, 16), sticky="ew")
        for text, command in (
            ("Test connection", self.test_connection),
            ("Sync now", lambda: self.app.sync_now(self._sync_finished)),
            ("Save SVG", self.save_svg),
        ):
            ctk.CTkButton(
                actions,
                text=text,
                height=32,
                command=command,
            ).pack(fill="x", pady=(0, 6))

        right = ctk.CTkFrame(lower, corner_radius=16)
        right.grid(row=0, column=1, padx=(10, 0), sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(
            right,
            text="Recent sync attempts",
            font=ctk.CTkFont(size=17, weight="bold"),
        ).grid(row=0, column=0, padx=16, pady=(16, 8), sticky="w")

        self._configure_tree_style()
        columns = ("time", "status", "days", "http", "message")
        self.sync_tree = ttk.Treeview(
            right,
            columns=columns,
            show="headings",
        )
        for key, title, width in (
            ("time", "Time", 120),
            ("status", "Status", 75),
            ("days", "Days", 55),
            ("http", "HTTP", 55),
            ("message", "Message", 310),
        ):
            self.sync_tree.heading(key, text=title)
            self.sync_tree.column(key, width=width, anchor="w")
        self.sync_tree.grid(
            row=1,
            column=0,
            padx=16,
            pady=(0, 16),
            sticky="nsew",
        )

    @staticmethod
    def _configure_tree_style() -> None:
        SessionsPage._configure_tree_style()

    def refresh(self) -> None:
        pending = self.app.store.unsynced_dates()
        self.pending_var.set(str(len(pending)))
        latest = self.app.store.latest_sync_run()
        if latest:
            dt = datetime.fromisoformat(latest["finished_at"]).astimezone(self.app.zone)
            self.last_sync_var.set(f"{dt:%Y-%m-%d %H:%M}\n{latest['status']}")
        else:
            self.last_sync_var.set("Never")
        for item in self.sync_tree.get_children():
            self.sync_tree.delete(item)
        for row in self.app.store.sync_runs(limit=100):
            started = datetime.fromisoformat(row["started_at"]).astimezone(self.app.zone)
            self.sync_tree.insert(
                "",
                "end",
                values=(
                    started.strftime("%m-%d %H:%M"),
                    row["status"],
                    f"{row['dates_synced']}/{row['dates_attempted']}",
                    row["http_status"] or "—",
                    row["message"],
                ),
            )

    def test_connection(self) -> None:
        self.connection_var.set("Checking…")

        def worker() -> None:
            try:
                result = self.app.pixela.test_connection()
                graph_name = result.data.get("name", self.app.config_data["graph_id"])
                text = f"Connected · HTTP {result.http_status}\n{graph_name}"
            except Exception as exc:
                text = f"Failed\n{exc}"
            self.after(0, lambda: self.connection_var.set(text))

        threading.Thread(target=worker, daemon=True).start()

    def _sync_finished(self, success: bool, message: str) -> None:
        self.connection_var.set("Connected" if success else "Sync error")
        self.preview_status_var.set(message)
        self.refresh()
        if success:
            self.refresh_preview()

    def refresh_preview(self) -> None:
        if not self.app.pixela.username or self.app.pixela.username == "change-me":
            self.preview_status_var.set("Configure Pixela first.")
            return
        self.preview_status_var.set("Loading official Pixela SVG…")
        self.preview_label.configure(image=None)

        def worker() -> None:
            try:
                svg_bytes, status, _duration = self.app.pixela.graph_svg(
                    mode=None,
                    transparent=False,
                )
                if resvg_py is None:
                    raise RuntimeError("Install resvg_py to render the SVG inside the app.")
                png_bytes = resvg_py.svg_to_bytes(
                    svg_string=svg_bytes.decode("utf-8"),
                    width=1080,
                )
                image = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
                ctk_image = ctk.CTkImage(light_image=image, dark_image=image, size=image.size)

                def show() -> None:
                    self.preview_image = ctk_image
                    self.preview_label.configure(image=ctk_image, text="")
                    self.preview_status_var.set(f"Official Pixela image · HTTP {status}")
                    self.connection_var.set(f"Connected · HTTP {status}")

                self.after(0, show)
            except Exception as exc:
                self.after(0, lambda: self.preview_status_var.set(f"Preview unavailable: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def graph_url(self) -> str:
        return (
            f"https://pixe.la/v1/users/{self.app.config_data['username']}"
            f"/graphs/{self.app.config_data['graph_id']}.html"
        )

    def open_graph(self) -> None:
        webbrowser.open(self.graph_url())

    def save_svg(self) -> None:
        path_text = filedialog.asksaveasfilename(
            title="Save official Pixela SVG",
            initialdir=EXPORT_DIR,
            initialfile=f"{self.app.config_data['graph_id']}.svg",
            defaultextension=".svg",
            filetypes=[("SVG image", "*.svg")],
        )
        if not path_text:
            return

        def worker() -> None:
            try:
                svg_bytes, _status, _duration = self.app.pixela.graph_svg(mode=None)
                Path(path_text).write_bytes(svg_bytes)
                self.after(0, lambda: messagebox.showinfo("SVG saved", path_text))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Save failed", str(exc)))

        threading.Thread(target=worker, daemon=True).start()


class DiagnosticsPage(PageBase):
    def __init__(self, master, app: FocusApp):
        super().__init__(
            master,
            app,
            "Diagnostics and stability",
            "See the local camera, Cloudflare connector, public route, dashboard, and watchdog in one place.",
        )
        self.overall_var = ctk.StringVar(value="Waiting for the first tunnel check…")
        self.checked_var = ctk.StringVar(value="")
        self.service_var = ctk.StringVar(value="Cloudflared: checking…")
        self.local_var = ctk.StringVar(value="Local camera: checking…")
        self.public_var = ctk.StringVar(value="Public camera: checking…")
        self.dashboard_var = ctk.StringVar(value="Dashboard: checking…")
        self.watchdog_var = ctk.StringVar(value="SYSTEM watchdog: checking…")
        self._build()

    def _build(self) -> None:
        body = ctk.CTkScrollableFrame(self, corner_radius=16)
        body.grid(row=1, column=0, padx=26, pady=(0, 24), sticky="nsew")
        body.grid_columnconfigure((0, 1), weight=1)

        summary = ctk.CTkFrame(body, corner_radius=14)
        summary.grid(row=0, column=0, columnspan=2, padx=8, pady=8, sticky="ew")
        summary.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            summary,
            textvariable=self.overall_var,
            font=ctk.CTkFont(size=19, weight="bold"),
            wraplength=900,
            justify="left",
        ).grid(row=0, column=0, padx=16, pady=(16, 5), sticky="w")
        ctk.CTkLabel(
            summary,
            textvariable=self.checked_var,
            text_color=("#64748b", "#94a3b8"),
        ).grid(row=1, column=0, padx=16, pady=(0, 14), sticky="w")

        cards = [
            ("Cloudflared Windows service", self.service_var, 1, 0),
            ("Local camera origin", self.local_var, 1, 1),
            ("Public Cloudflare route", self.public_var, 2, 0),
            ("Cloud dashboard", self.dashboard_var, 2, 1),
            ("Automatic SYSTEM watchdog", self.watchdog_var, 3, 0),
        ]
        for title, variable, row, column in cards:
            card = ctk.CTkFrame(body, corner_radius=14)
            card.grid(row=row, column=column, padx=8, pady=8, sticky="nsew")
            card.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                card,
                text=title,
                font=ctk.CTkFont(size=15, weight="bold"),
            ).grid(row=0, column=0, padx=14, pady=(14, 6), sticky="w")
            ctk.CTkLabel(
                card,
                textvariable=variable,
                wraplength=480,
                justify="left",
            ).grid(row=1, column=0, padx=14, pady=(0, 14), sticky="w")

        actions = ctk.CTkFrame(body, corner_radius=14)
        actions.grid(row=3, column=1, padx=8, pady=8, sticky="nsew")
        actions.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkLabel(
            actions,
            text="Actions",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, padx=14, pady=(14, 8), sticky="w")
        ctk.CTkButton(
            actions,
            text="Refresh now",
            command=self.app.refresh_tunnel_health,
        ).grid(row=1, column=0, padx=(14, 5), pady=5, sticky="ew")
        ctk.CTkButton(
            actions,
            text="Full diagnosis ZIP",
            command=self.app.run_full_diagnosis,
        ).grid(row=1, column=1, padx=(5, 14), pady=5, sticky="ew")
        ctk.CTkButton(
            actions,
            text="Repair Cloudflare Tunnel",
            command=self.app.run_tunnel_repair,
        ).grid(row=2, column=0, padx=(14, 5), pady=5, sticky="ew")
        ctk.CTkButton(
            actions,
            text="Install auto-recovery",
            command=self.app.install_tunnel_watchdog,
        ).grid(row=2, column=1, padx=(5, 14), pady=5, sticky="ew")
        ctk.CTkButton(
            actions,
            text="Remove auto-recovery",
            fg_color=("#e2e8f0", "#2b3542"),
            text_color=("#1f2937", "#e5e7eb"),
            hover_color=("#cbd5e1", "#3b4756"),
            command=self.app.remove_tunnel_watchdog,
        ).grid(row=3, column=0, columnspan=2, padx=14, pady=(5, 14), sticky="ew")

        ctk.CTkLabel(
            body,
            text="Technical details",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).grid(row=4, column=0, columnspan=2, padx=12, pady=(14, 6), sticky="w")
        self.details = ctk.CTkTextbox(body, height=270, wrap="word")
        self.details.grid(row=5, column=0, columnspan=2, padx=8, pady=(0, 12), sticky="ew")
        self.details.insert(
            "1.0",
            "The first health result will appear here. This page never reads or displays the Cloudflare tunnel token.",
        )
        self.details.configure(state="disabled")

        ctk.CTkLabel(
            body,
            text=(
                "The automatic watchdog runs as a Windows Scheduled Task under SYSTEM. "
                "It restarts cloudflared only after repeated public-route failures while the local camera is healthy. "
                "It does not reset adapters, Winsock, v2rayN, Tailscale, or Windows proxy settings."
            ),
            wraplength=980,
            justify="left",
            text_color=("#64748b", "#94a3b8"),
        ).grid(row=6, column=0, columnspan=2, padx=12, pady=(0, 16), sticky="w")

    @staticmethod
    def _endpoint_text(name: str, endpoint) -> str:
        if not endpoint.url:
            return f"{name}: not configured"
        status = endpoint.status or "no response"
        extra = endpoint.error or endpoint.body_preview
        if len(extra) > 180:
            extra = extra[:180] + "…"
        suffix = f"\n{extra}" if extra else ""
        return f"{name}: HTTP {status} · {endpoint.elapsed_ms} ms{suffix}"

    def apply_health(self, health: TunnelHealth) -> None:
        self.overall_var.set(f"{health.headline} — {health.detail}")
        self.checked_var.set(f"Last checked: {health.checked_at}")
        self.service_var.set(
            f"Installed: {'yes' if health.service_installed else 'no'}\n"
            f"Running: {'yes' if health.service_running else 'no'}\n"
            f"Start mode: {health.service_start_mode or '-'}\n"
            f"PID: {health.service_pid or '-'}"
        )
        self.local_var.set(self._endpoint_text("Local camera", health.local_camera))
        self.public_var.set(self._endpoint_text("Public camera", health.public_camera))
        self.dashboard_var.set(self._endpoint_text("Dashboard", health.dashboard))
        self.watchdog_var.set(
            f"Installed: {'yes' if health.watchdog_installed else 'no'}\n"
            f"Last check: {health.watchdog_last_check or '-'}\n"
            f"Last action: {health.watchdog_last_action or '-'}"
        )
        lines = [
            f"Overall: {health.overall}",
            f"Headline: {health.headline}",
            f"Detail: {health.detail}",
            "",
            self._endpoint_text("Local camera", health.local_camera),
            "",
            self._endpoint_text("Public camera", health.public_camera),
            "",
            self._endpoint_text("Cloud dashboard", health.dashboard),
            "",
            f"Cloudflared service installed: {health.service_installed}",
            f"Cloudflared service running: {health.service_running}",
            f"Cloudflared start mode: {health.service_start_mode}",
            f"Cloudflared PID: {health.service_pid}",
            f"SYSTEM watchdog installed: {health.watchdog_installed}",
            f"SYSTEM watchdog last check: {health.watchdog_last_check or '-'}",
            f"SYSTEM watchdog last action: {health.watchdog_last_action or '-'}",
        ]
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", "\n".join(lines))
        self.details.configure(state="disabled")

    def refresh(self) -> None:
        if self.app._last_tunnel_health is not None:
            self.apply_health(self.app._last_tunnel_health)
        self.app.refresh_tunnel_health()


class SettingsPage(PageBase):
    def __init__(self, master, app: FocusApp):
        super().__init__(
            master,
            app,
            "Settings",
            "Keep the default screen simple; customize the workflow only where it helps.",
        )
        self.vars: dict[str, ctk.Variable] = {}
        self.camera_password_var = ctk.StringVar()
        self.camera_password_confirm_var = ctk.StringVar()
        self._build()

    def _string_var(self, key: str) -> ctk.StringVar:
        variable = ctk.StringVar(value=str(self.app.config_data.get(key, "")))
        self.vars[key] = variable
        return variable

    def _bool_var(self, key: str) -> ctk.BooleanVar:
        variable = ctk.BooleanVar(value=bool(self.app.config_data.get(key, False)))
        self.vars[key] = variable
        return variable

    def _build(self) -> None:
        scroll = ctk.CTkScrollableFrame(self, corner_radius=16)
        scroll.grid(row=1, column=0, padx=26, pady=(0, 24), sticky="nsew")
        scroll.grid_columnconfigure((0, 1), weight=1)

        timer = self._section(scroll, "Timer and Pomodoro", 0, 0)
        self._entry(timer, "Focus minutes", "focus_minutes", 0)
        self._entry(timer, "Short break minutes", "short_break_minutes", 1)
        self._entry(timer, "Long break minutes", "long_break_minutes", 2)
        self._entry(timer, "Focus blocks before long break", "sessions_before_long_break", 3)
        self._entry(timer, "Daily goal in minutes", "daily_goal_minutes", 4)
        self._entry(timer, "Minimum minutes to log", "minimum_log_minutes", 5)
        self._switch(timer, "Auto-start breaks", "auto_start_breaks", 6)
        self._switch(timer, "Auto-start focus after breaks", "auto_start_focus", 7)
        self._switch(timer, "Completion sound", "sound_enabled", 8)

        interface = self._section(scroll, "Interface", 0, 1)
        ctk.CTkLabel(interface, text="Appearance").grid(row=0, column=0, padx=14, pady=(12, 4), sticky="w")
        appearance = self._string_var("appearance")
        ctk.CTkOptionMenu(interface, values=["System", "Light", "Dark"], variable=appearance).grid(row=1, column=0, padx=14, sticky="ew")
        ctk.CTkLabel(interface, text="Accent theme (restart required)").grid(row=2, column=0, padx=14, pady=(12, 4), sticky="w")
        accent = self._string_var("accent_theme")
        ctk.CTkOptionMenu(interface, values=["blue", "dark-blue", "green"], variable=accent).grid(row=3, column=0, padx=14, sticky="ew")
        self._switch(interface, "Always on top", "always_on_top", 4)
        ctk.CTkLabel(interface, text="Categories (one per line)").grid(row=6, column=0, padx=14, pady=(12, 4), sticky="w")
        self.categories_text = ctk.CTkTextbox(interface, height=150)
        self.categories_text.grid(row=7, column=0, padx=14, pady=(0, 14), sticky="ew")
        self.categories_text.insert("1.0", "\n".join(self.app.config_data.get("categories", [])))

        pixela = self._section(scroll, "Pixela", 1, 0)
        self._entry(pixela, "Username", "username", 0)
        self._entry(pixela, "Graph ID", "graph_id", 1)
        self._entry(pixela, "Timezone", "timezone", 2)
        ctk.CTkLabel(pixela, text="Token").grid(row=6, column=0, padx=14, pady=(12, 4), sticky="w")
        token = self._string_var("token")
        token_entry = ctk.CTkEntry(pixela, textvariable=token, show="•")
        token_entry.grid(row=7, column=0, padx=14, sticky="ew")
        ctk.CTkLabel(
            pixela,
            text="The graph should remain an integer minutes graph. The app displays hour summaries locally when useful.",
            wraplength=430,
            justify="left",
            text_color=("#64748b", "#94a3b8"),
        ).grid(row=8, column=0, padx=14, pady=(14, 14), sticky="w")

        cloud = self._section(scroll, "Cloud dashboard", 2, 0)
        self._switch(
            cloud,
            "Publish read-only monitoring data",
            "cloud_dashboard_enabled",
            0,
        )
        self._entry(
            cloud,
            "Cloudflare Pages URL",
            "cloud_dashboard_url",
            1,
        )
        ctk.CTkLabel(cloud, text="Desktop write key").grid(
            row=5, column=0, padx=14, pady=(10, 4), sticky="w"
        )
        cloud_key = self._string_var("cloud_desktop_write_key")
        ctk.CTkEntry(
            cloud,
            textvariable=cloud_key,
            show="•",
        ).grid(row=6, column=0, padx=14, sticky="ew")
        self._entry(
            cloud,
            "Update seconds while timer is running",
            "cloud_running_update_seconds",
            3,
        )
        self._entry(
            cloud,
            "Update seconds while idle",
            "cloud_idle_update_seconds",
            4,
        )
        ctk.CTkLabel(
            cloud,
            text=(
                "Only read-only dashboard snapshots are uploaded. "
                "Your Pixela token is never sent to this cloud service."
            ),
            wraplength=430,
            justify="left",
            text_color=("#64748b", "#94a3b8"),
        ).grid(row=11, column=0, padx=14, pady=(12, 8), sticky="w")
        ctk.CTkButton(
            cloud,
            text="Save and test cloud upload",
            command=self.save_and_test_cloud,
        ).grid(row=12, column=0, padx=14, pady=(0, 14), sticky="ew")

        camera = self._section(
            scroll,
            "Private camera over Cloudflare Tunnel or Tailscale",
            2,
            1,
        )
        self._switch(
            camera,
            "Allow private camera",
            "remote_camera_enabled",
            0,
        )
        self._entry(
            camera,
            "Camera public/private URL",
            "tailscale_camera_url",
            1,
        )
        self._entry(
            camera,
            "Local camera server port",
            "tailscale_camera_port",
            2,
        )
        self._entry(
            camera,
            "Allowed website origins (comma-separated)",
            "tailscale_camera_allowed_origin",
            3,
        )
        self._entry(
            camera,
            "Allowed Tailscale users (optional; ignored for Cloudflare-only mode)",
            "tailscale_camera_allowed_users",
            4,
        )
        self._switch(
            camera,
            "Require Tailscale identity headers",
            "tailscale_camera_require_identity",
            5,
        )
        self._entry(
            camera,
            "Camera index",
            "remote_camera_index",
            6,
        )
        self._entry(
            camera,
            "Video width",
            "remote_camera_width",
            7,
        )
        self._entry(
            camera,
            "Video height",
            "remote_camera_height",
            8,
        )
        self._entry(
            camera,
            "Frames per second (2–20)",
            "remote_camera_fps",
            9,
        )
        self._entry(
            camera,
            "JPEG quality (40–90)",
            "remote_camera_jpeg_quality",
            10,
        )
        self._entry(
            camera,
            "Disconnect timeout seconds (8–120)",
            "remote_camera_idle_seconds",
            11,
        )
        self._entry(
            camera,
            "Maximum viewer session minutes (1–60)",
            "remote_camera_session_minutes",
            12,
        )

        password_state = (
            "A private camera viewer password is configured."
            if has_camera_password(self.app.config_data)
            else "No private camera viewer password is configured."
        )
        ctk.CTkLabel(
            camera,
            text=password_state,
            text_color=("#64748b", "#94a3b8"),
        ).grid(
            row=27,
            column=0,
            padx=14,
            pady=(12, 4),
            sticky="w",
        )
        ctk.CTkLabel(
            camera,
            text="New camera viewer password",
        ).grid(
            row=28,
            column=0,
            padx=14,
            pady=(8, 4),
            sticky="w",
        )
        ctk.CTkEntry(
            camera,
            textvariable=self.camera_password_var,
            show="•",
            placeholder_text="Leave blank to keep the current password",
        ).grid(row=29, column=0, padx=14, sticky="ew")
        ctk.CTkLabel(
            camera,
            text="Confirm new password",
        ).grid(
            row=30,
            column=0,
            padx=14,
            pady=(8, 4),
            sticky="w",
        )
        ctk.CTkEntry(
            camera,
            textvariable=self.camera_password_confirm_var,
            show="•",
        ).grid(row=31, column=0, padx=14, sticky="ew")
        ctk.CTkLabel(
            camera,
            text=(
                "The local camera server listens only on 127.0.0.1. You can expose "
                "it privately with Tailscale Serve, or publish it under your own "
                "hostname with Cloudflare Tunnel. For a normal browser route, "
                "turn off the Tailscale identity requirement and "
                "keep the separate camera password enabled."
            ),
            wraplength=430,
            justify="left",
            text_color=("#64748b", "#94a3b8"),
        ).grid(
            row=32,
            column=0,
            padx=14,
            pady=(12, 8),
            sticky="w",
        )

        camera_buttons = ctk.CTkFrame(
            camera,
            fg_color="transparent",
        )
        camera_buttons.grid(
            row=33,
            column=0,
            padx=14,
            pady=(0, 8),
            sticky="ew",
        )
        camera_buttons.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(
            camera_buttons,
            text="Configure Tailscale Serve (optional)",
            command=self.save_and_configure_tailscale,
        ).grid(row=0, column=0, padx=(0, 4), sticky="ew")
        ctk.CTkButton(
            camera_buttons,
            text="Open private viewer",
            command=self.open_private_camera_page,
        ).grid(row=0, column=1, padx=(4, 0), sticky="ew")

        ctk.CTkButton(
            camera,
            text="Save settings and test local camera",
            command=self.save_and_test_camera,
        ).grid(
            row=34,
            column=0,
            padx=14,
            pady=(0, 8),
            sticky="ew",
        )
        ctk.CTkButton(
            camera,
            text="Show Tailscale Serve status",
            fg_color=("#e2e8f0", "#2b3542"),
            text_color=("#1f2937", "#e5e7eb"),
            hover_color=("#cbd5e1", "#3b4756"),
            command=self.app.show_tailscale_serve_status,
        ).grid(
            row=35,
            column=0,
            padx=14,
            pady=(0, 14),
            sticky="ew",
        )

        data = self._section(scroll, "Data locations", 1, 1)
        ctk.CTkLabel(
            data,
            text=(
                f"SQLite database\n{DB_PATH}\n\n"
                f"Automatic readable CSV copies\n{READABLE_DIR}\n\n"
                f"Manual exports and backups\n{EXPORT_DIR}\n\n"
                f"Schedule CSV\n{APP_DIR / 'schedule.csv'}"
            ),
            justify="left",
            anchor="nw",
            wraplength=430,
        ).grid(row=0, column=0, padx=14, pady=14, sticky="nw")
        ctk.CTkButton(data, text="Open app folder", command=lambda: self.open_folder(APP_DIR)).grid(row=1, column=0, padx=14, pady=(0, 8), sticky="ew")
        ctk.CTkButton(data, text="Open readable CSV folder", command=lambda: self.open_folder(READABLE_DIR)).grid(row=2, column=0, padx=14, pady=(0, 8), sticky="ew")
        ctk.CTkButton(
            data,
            text="Open Schedule tab",
            command=lambda: self.app.show_page("Schedule"),
        ).grid(row=3, column=0, padx=14, pady=(0, 14), sticky="ew")

        stability = self._section(scroll, "Stability and tunnel monitoring", 3, 0)
        self._switch(
            stability,
            "Monitor the Cloudflare Tunnel in the app",
            "tunnel_monitor_enabled",
            0,
        )
        self._entry(
            stability,
            "Health-check interval in seconds (10–600)",
            "tunnel_check_seconds",
            1,
        )
        self._switch(
            stability,
            "Notify when the tunnel disconnects or recovers",
            "tunnel_notifications_enabled",
            2,
        )
        ctk.CTkButton(
            stability,
            text="Open Diagnostics page",
            command=lambda: self.app.show_page("Diagnostics"),
        ).grid(row=7, column=0, padx=14, pady=(8, 6), sticky="ew")
        ctk.CTkButton(
            stability,
            text="Install automatic SYSTEM watchdog",
            command=self.app.install_tunnel_watchdog,
        ).grid(row=8, column=0, padx=14, pady=(0, 14), sticky="ew")

        buttons = ctk.CTkFrame(scroll, fg_color="transparent")
        buttons.grid(row=4, column=0, columnspan=2, padx=8, pady=18)
        ctk.CTkButton(buttons, text="Save settings", width=160, height=42, command=self.save).pack(side="left", padx=6)
        ctk.CTkButton(buttons, text="Save and test Pixela", width=160, height=42, command=self.save_and_test).pack(side="left", padx=6)

    @staticmethod
    def _section(master, title: str, row: int, column: int) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(master, corner_radius=14)
        frame.grid(row=row, column=column, padx=8, pady=8, sticky="nsew")
        frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(frame, text=title, font=ctk.CTkFont(size=17, weight="bold")).grid(row=0, column=0, padx=14, pady=(14, 4), sticky="w")
        return frame

    def _entry(self, master, label: str, key: str, logical_row: int) -> None:
        row = logical_row * 2 + 1
        ctk.CTkLabel(master, text=label).grid(row=row, column=0, padx=14, pady=(10, 4), sticky="w")
        ctk.CTkEntry(master, textvariable=self._string_var(key)).grid(row=row + 1, column=0, padx=14, sticky="ew")

    def _switch(self, master, label: str, key: str, logical_row: int) -> None:
        row = logical_row * 2 + 1
        ctk.CTkSwitch(master, text=label, variable=self._bool_var(key)).grid(row=row, column=0, padx=14, pady=10, sticky="w")

    def save(self, show_message: bool = True) -> bool:
        numeric_keys = [
            "focus_minutes",
            "short_break_minutes",
            "long_break_minutes",
            "sessions_before_long_break",
            "daily_goal_minutes",
            "minimum_log_minutes",
            "cloud_running_update_seconds",
            "cloud_idle_update_seconds",
            "remote_camera_index",
            "remote_camera_width",
            "remote_camera_height",
            "remote_camera_fps",
            "remote_camera_jpeg_quality",
            "remote_camera_idle_seconds",
            "remote_camera_session_minutes",
            "tailscale_camera_port",
            "tunnel_check_seconds",
        ]
        new_config = dict(self.app.config_data)
        try:
            for key, variable in self.vars.items():
                if key in numeric_keys:
                    new_config[key] = int(variable.get())
                elif isinstance(variable, ctk.BooleanVar):
                    new_config[key] = bool(variable.get())
                else:
                    new_config[key] = str(variable.get()).strip()
            ZoneInfo(new_config["timezone"])
        except (ValueError, KeyError) as exc:
            messagebox.showerror("Invalid settings", f"Check the numeric values and timezone.\n\n{exc}")
            return False

        if new_config["focus_minutes"] < 1 or new_config["short_break_minutes"] < 1:
            messagebox.showerror("Invalid settings", "Timer lengths must be at least one minute.")
            return False

        if not 10 <= new_config["tunnel_check_seconds"] <= 600:
            messagebox.showerror(
                "Invalid settings",
                "Tunnel health-check interval must be from 10 to 600 seconds.",
            )
            return False

        if new_config["remote_camera_index"] < 0:
            messagebox.showerror("Invalid settings", "Camera index cannot be negative.")
            return False
        if not 2 <= new_config["remote_camera_fps"] <= 20:
            messagebox.showerror(
                "Invalid settings",
                "Camera FPS must be from 2 to 20.",
            )
            return False
        if not 40 <= new_config["remote_camera_jpeg_quality"] <= 90:
            messagebox.showerror(
                "Invalid settings",
                "JPEG quality must be from 40 to 90.",
            )
            return False
        if not 8 <= new_config["remote_camera_idle_seconds"] <= 120:
            messagebox.showerror(
                "Invalid settings",
                "Camera disconnect timeout must be from 8 to 120 seconds.",
            )
            return False
        if not 1 <= new_config["remote_camera_session_minutes"] <= 60:
            messagebox.showerror(
                "Invalid settings",
                "Maximum camera viewing time must be from 1 to 60 minutes.",
            )
            return False
        if not 1024 <= new_config["tailscale_camera_port"] <= 65535:
            messagebox.showerror(
                "Invalid settings",
                "The local camera server port must be from 1024 to 65535.",
            )
            return False
        if (
            new_config["remote_camera_width"] < 320
            or new_config["remote_camera_height"] < 240
        ):
            messagebox.showerror(
                "Invalid settings",
                "Camera size must be at least 320×240.",
            )
            return False

        new_password = self.camera_password_var.get()
        confirmation = self.camera_password_confirm_var.get()
        if new_password or confirmation:
            if new_password != confirmation:
                messagebox.showerror(
                    "Password mismatch",
                    "The camera viewer passwords do not match.",
                )
                return False
            try:
                new_config.update(derive_camera_password(new_password))
            except ValueError as exc:
                messagebox.showerror("Invalid camera password", str(exc))
                return False
            self.camera_password_var.set("")
            self.camera_password_confirm_var.set("")

        if (
            bool(new_config.get("remote_camera_enabled", False))
            and not has_camera_password(new_config)
        ):
            messagebox.showerror(
                "Camera password required",
                "Set a camera viewer password before enabling the private camera.",
            )
            return False

        camera_url = str(
            new_config.get("tailscale_camera_url", "")
        ).strip().rstrip("/")
        if camera_url:
            from urllib.parse import urlparse

            try:
                parsed_camera_url = urlparse(camera_url)
            except ValueError:
                parsed_camera_url = None

            is_valid_https_url = bool(
                parsed_camera_url
                and parsed_camera_url.scheme.lower() == "https"
                and parsed_camera_url.netloc
                and not parsed_camera_url.username
                and not parsed_camera_url.password
            )
            if not is_valid_https_url:
                messagebox.showerror(
                    "Invalid camera URL",
                    "Use a complete HTTPS address, for example "
                    "https://camera.example.com or "
                    "https://your-laptop.your-tailnet.ts.net",
                )
                return False
            new_config["tailscale_camera_url"] = camera_url

        categories = [line.strip() for line in self.categories_text.get("1.0", "end").splitlines() if line.strip()]
        new_config["categories"] = list(dict.fromkeys(categories)) or ["General"]
        old_accent = self.app.config_data.get("accent_theme")
        self.app.config_data = new_config
        self.app.save_config_data()
        self.app._configure_cloud_publisher()
        self.app._configure_cloud_syncer()
        self.app.camera_enabled_var.set(
            bool(new_config.get("remote_camera_enabled", False))
        )
        self.app._configure_remote_camera()
        self.app.tunnel_monitor.request_check()
        self.app.publish_cloud_snapshot_now()
        ctk.set_appearance_mode(new_config["appearance"])
        self.app.attributes("-topmost", bool(new_config["always_on_top"]))
        self.app.refresh_data_views()
        if show_message:
            extra = "\nRestart the app to apply the accent theme." if old_accent != new_config["accent_theme"] else ""
            messagebox.showinfo("Settings saved", "Your settings were saved." + extra)
        return True

    def save_and_test_camera(self) -> None:
        if not self.save(show_message=False):
            return
        self.app.test_local_camera()

    def save_and_configure_tailscale(self) -> None:
        if not self.save(show_message=False):
            return
        self.app.configure_tailscale_camera()

    def open_private_camera_page(self) -> None:
        if not self.save(show_message=False):
            return
        self.app.open_private_camera()

    def save_and_test_cloud(self) -> None:
        if not self.save(show_message=False):
            return
        self.app.test_cloud_dashboard()

    def save_and_test(self) -> None:
        if not self.save(show_message=False):
            return
        self.app.show_page("Pixela")
        pixela_page = self.app.pages["Pixela"]
        if isinstance(pixela_page, PixelaPage):
            pixela_page.test_connection()

    def open_schedule_file(self) -> None:
        path = APP_DIR / "schedule.csv"
        if not path.exists():
            path.write_text(
                "date,start,end,title,category\n",
                encoding="utf-8",
            )
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')

    @staticmethod
    def open_folder(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')


def configure_cli() -> None:
    config = load_config(CONFIG_PATH)
    print("Pixela Focus Studio configuration")
    username = input(f"Pixela username [{config['username']}]: ").strip()
    if username:
        config["username"] = username
    token = getpass.getpass("Pixela token (hidden; leave blank to keep current): ").strip()
    if token:
        config["token"] = token
    graph_id = input(f"Graph ID [{config['graph_id']}]: ").strip()
    if graph_id:
        config["graph_id"] = graph_id
    timezone_name = input(f"Timezone [{config['timezone']}]: ").strip()
    if timezone_name:
        ZoneInfo(timezone_name)
        config["timezone"] = timezone_name
    save_config(CONFIG_PATH, config)
    print(f"Saved: {CONFIG_PATH}")
    print("Run: python app.py")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pixela Focus Studio")
    parser.add_argument("command", nargs="?", choices=("run", "configure"), default="run")
    args = parser.parse_args()
    if args.command == "configure":
        configure_cli()
        return

    config = load_config(CONFIG_PATH)
    ctk.set_appearance_mode(str(config.get("appearance", "System")))
    ctk.set_default_color_theme(str(config.get("accent_theme", "blue")))
    app = FocusApp(config)
    app.mainloop()


if __name__ == "__main__":
    main()
