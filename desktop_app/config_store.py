from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

DEFAULT_CONFIG = {
    "username": "change-me",
    "token": "",
    "graph_id": "focus-minutes",
    "timezone": "Asia/Tehran",
    "appearance": "System",
    "accent_theme": "blue",
    "focus_minutes": 25,
    "short_break_minutes": 5,
    "long_break_minutes": 15,
    "sessions_before_long_break": 4,
    "daily_goal_minutes": 120,
    "minimum_log_minutes": 1,
    "auto_start_breaks": False,
    "auto_start_focus": False,
    "always_on_top": False,
    "sound_enabled": True,
    "monitor_enabled": True,
    "monitor_port": 8765,
    "web_dashboard_auto_open": True,
    "cloud_dashboard_enabled": False,
    "cloud_dashboard_url": "",
    "cloud_desktop_write_key": "",
    "cloud_running_update_seconds": 4,
    "cloud_idle_update_seconds": 30,
    "cloud_two_way_sync_enabled": True,
    "cloud_sync_running_seconds": 5,
    "cloud_sync_idle_seconds": 30,
    "cloud_device_id": "",
    "remote_camera_enabled": False,
    "remote_camera_index": 0,
    "remote_camera_width": 960,
    "remote_camera_height": 540,
    "remote_camera_fps": 10,
    "remote_camera_jpeg_quality": 72,
    "remote_camera_idle_seconds": 15,
    "remote_camera_session_minutes": 10,
    "tailscale_camera_port": 8788,
    "tailscale_camera_url": "",
    "tailscale_camera_allowed_origin": "",
    "tailscale_camera_require_identity": False,
    "tailscale_camera_allowed_users": "",
    "remote_camera_password_salt": "",
    "remote_camera_password_hash": "",
    "remote_camera_password_iterations": 100000,
    "categories": [
        "Research",
        "Coursework",
        "Coding",
        "Reading",
        "Writing",
        "Communication",
        "Meetings",
        "Admin",
        "Personal",
        "Break",
    ],
}


def load_config(path: Path) -> dict:
    if not path.exists():
        save_config(path, DEFAULT_CONFIG.copy())

    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        stored = {}

    config = DEFAULT_CONFIG | stored
    config["username"] = os.getenv("PIXELA_USERNAME", config["username"])
    config["token"] = os.getenv("PIXELA_TOKEN", config["token"])
    config["graph_id"] = os.getenv("PIXELA_GRAPH_ID", config["graph_id"])

    try:
        ZoneInfo(str(config["timezone"]))
    except Exception:
        config["timezone"] = DEFAULT_CONFIG["timezone"]

    if not isinstance(config.get("categories"), list):
        config["categories"] = DEFAULT_CONFIG["categories"].copy()

    if not str(config.get("cloud_device_id", "")).strip():
        device_id = uuid.uuid4().hex
        config["cloud_device_id"] = device_id
        persisted = DEFAULT_CONFIG | stored
        persisted["cloud_device_id"] = device_id
        save_config(path, persisted)

    return config


def save_config(path: Path, config: dict) -> None:
    path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
