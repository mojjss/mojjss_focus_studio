from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


@dataclass(slots=True)
class EndpointResult:
    url: str
    status: int = 0
    ok: bool = False
    elapsed_ms: int = 0
    error: str = ""
    body_preview: str = ""


@dataclass(slots=True)
class TunnelHealth:
    checked_at: str
    overall: str
    headline: str
    detail: str
    service_installed: bool
    service_running: bool
    service_start_mode: str
    service_pid: int
    local_camera: EndpointResult
    public_camera: EndpointResult
    dashboard: EndpointResult
    watchdog_installed: bool
    watchdog_last_action: str
    watchdog_last_check: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TunnelHealthMonitor:
    """Lightweight status monitor for the local camera and Cloudflare Tunnel.

    It never reads or stores the tunnel token and never changes networking.
    The separate elevated watchdog/repair tools perform service changes.
    """

    def __init__(
        self,
        config_provider: Callable[[], dict[str, Any]],
        callback: Callable[[TunnelHealth], None],
    ) -> None:
        self._config_provider = config_provider
        self._callback = callback
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._check_lock = threading.Lock()
        self._last_health: TunnelHealth | None = None

    @property
    def last_health(self) -> TunnelHealth | None:
        return self._last_health

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="focus-tunnel-monitor",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def request_check(self) -> None:
        self._wake.set()

    def check_now(self) -> TunnelHealth:
        with self._check_lock:
            health = collect_tunnel_health(self._config_provider())
            self._last_health = health
            return health

    def _loop(self) -> None:
        while not self._stop.is_set():
            config = self._config_provider()
            enabled = bool(config.get("tunnel_monitor_enabled", True))
            if enabled:
                try:
                    health = self.check_now()
                    self._callback(health)
                except Exception as exc:
                    now = datetime.now(timezone.utc).isoformat()
                    empty = EndpointResult(url="", error=str(exc))
                    self._callback(
                        TunnelHealth(
                            checked_at=now,
                            overall="unknown",
                            headline="Tunnel monitor could not complete",
                            detail=str(exc),
                            service_installed=False,
                            service_running=False,
                            service_start_mode="",
                            service_pid=0,
                            local_camera=empty,
                            public_camera=empty,
                            dashboard=empty,
                            watchdog_installed=False,
                            watchdog_last_action="",
                            watchdog_last_check="",
                        )
                    )
            try:
                interval = int(config.get("tunnel_check_seconds", 30))
            except (TypeError, ValueError):
                interval = 30
            interval = max(10, min(interval, 600))
            self._wake.wait(interval)
            self._wake.clear()


def _clean_base_url(value: object) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    try:
        parsed = urllib.parse.urlparse(text)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return text


def _safe_body_preview(data: bytes, limit: int = 500) -> str:
    text = data[:limit].decode("utf-8", errors="replace")
    text = re.sub(r"(?i)eyJ[A-Za-z0-9._~-]{20,}", "<REDACTED>", text)
    text = re.sub(
        r"(?i)((?:token|password|secret|key)\s*[=:]\s*)\S+",
        r"\1<REDACTED>",
        text,
    )
    return text.strip()


def check_endpoint(url: str, timeout: float = 8.0) -> EndpointResult:
    if not url:
        return EndpointResult(url="", error="Not configured")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mojjss-Focus-Studio/5.6",
            "Accept": "application/json,text/plain,*/*",
            "Cache-Control": "no-cache",
        },
        method="GET",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.perf_counter()
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(2048)
            status = int(getattr(response, "status", 200))
            return EndpointResult(
                url=url,
                status=status,
                ok=200 <= status < 400,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                body_preview=_safe_body_preview(body),
            )
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(2048)
        except Exception:
            body = b""
        return EndpointResult(
            url=url,
            status=int(exc.code),
            ok=False,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            error=str(exc.reason or exc),
            body_preview=_safe_body_preview(body),
        )
    except Exception as exc:
        return EndpointResult(
            url=url,
            status=0,
            ok=False,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            error=f"{type(exc).__name__}: {exc}",
        )


def _windows_service_state() -> tuple[bool, bool, str, int]:
    if os.name != "nt":
        return False, False, "unsupported", 0
    try:
        result = subprocess.run(
            ["sc.exe", "queryex", "Cloudflared"],
            capture_output=True,
            text=True,
            timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except Exception:
        return False, False, "unknown", 0
    text = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0 or "FAILED 1060" in text:
        return False, False, "missing", 0
    running = bool(re.search(r"STATE\s*:\s*\d+\s+RUNNING", text, re.I))
    pid_match = re.search(r"PID\s*:\s*(\d+)", text, re.I)
    pid = int(pid_match.group(1)) if pid_match else 0
    start_mode = "unknown"
    try:
        qc = subprocess.run(
            ["sc.exe", "qc", "Cloudflared"],
            capture_output=True,
            text=True,
            timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        mode_match = re.search(r"START_TYPE\s*:\s*\d+\s+([^\r\n]+)", qc.stdout, re.I)
        if mode_match:
            start_mode = mode_match.group(1).strip()
    except Exception:
        pass
    return True, running, start_mode, pid


def _watchdog_status() -> tuple[bool, str, str]:
    base = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
    status_path = base / "MojjssFocusStudio" / "tunnel_watchdog_status.json"
    if not status_path.exists():
        return False, "", ""
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
        return (
            True,
            str(data.get("last_action", "")),
            str(data.get("checked_at", "")),
        )
    except Exception:
        return True, "Status file is unreadable", ""


def collect_tunnel_health(config: dict[str, Any]) -> TunnelHealth:
    try:
        port = int(config.get("tailscale_camera_port", 8788))
    except (TypeError, ValueError):
        port = 8788
    port = max(1, min(port, 65535))
    local_url = f"http://127.0.0.1:{port}/api/health"

    camera_base = _clean_base_url(config.get("tailscale_camera_url", ""))
    dashboard_base = _clean_base_url(config.get("cloud_dashboard_url", ""))
    public_url = f"{camera_base}/api/health" if camera_base else ""
    dashboard_url = f"{dashboard_base}/api/health" if dashboard_base else ""

    service_installed, service_running, start_mode, pid = _windows_service_state()
    local = check_endpoint(local_url, timeout=4.5)
    public = check_endpoint(public_url, timeout=8.0)
    dashboard = check_endpoint(dashboard_url, timeout=8.0)
    watchdog_installed, watchdog_action, watchdog_check = _watchdog_status()

    camera_enabled = bool(config.get("remote_camera_enabled", False))
    body = f"{public.body_preview} {public.error}".lower()

    if not camera_base:
        overall = "not_configured"
        headline = "Tunnel URL is not configured"
        detail = "Set the camera HTTPS URL in Settings before monitoring the public route."
    elif not camera_enabled:
        overall = "not_enabled"
        headline = "Private camera monitoring is disabled"
        detail = "Enable private camera access in Settings when you want the local origin and tunnel monitored."
    elif not service_installed:
        overall = "service_missing"
        headline = "Cloudflared service is missing"
        detail = "Install or repair the Cloudflared Windows service."
    elif not service_running:
        overall = "service_down"
        headline = "Cloudflared service is stopped"
        detail = "The connector cannot reach Cloudflare until the service is running."
    elif public.status == 200:
        overall = "healthy" if local.status == 200 else "public_healthy_origin_uncertain"
        headline = "Cloudflare Tunnel is online"
        detail = (
            "The public camera health endpoint is reachable."
            if local.status == 200
            else "The public route responds, but the local camera health check is not currently healthy."
        )
    elif public.status in {401, 403}:
        overall = "access_protected"
        headline = "Tunnel reachable through Cloudflare Access"
        detail = "The route is reachable, but the unauthenticated health probe was denied."
    elif public.status == 502:
        overall = "origin_down"
        headline = "Tunnel connected, local camera unavailable"
        detail = "Cloudflare can see the connector, but cloudflared cannot reach the local camera origin."
    elif public.status in {530, 503} or "1033" in body or "argo tunnel" in body:
        overall = "tunnel_down"
        headline = "No healthy Cloudflare connector"
        detail = "The public route indicates a disconnected tunnel or error 1033."
    elif local.status != 200 and camera_enabled:
        overall = "origin_down"
        headline = "Local camera server is unavailable"
        detail = "Open Focus Studio and confirm that private camera access is enabled."
    elif public.status == 404:
        overall = "route_reachable"
        headline = "Tunnel route is connected"
        detail = "Cloudflare answered, but the configured health endpoint was not found. Check the camera URL and route path."
    elif public.status == 0:
        overall = "network_error"
        headline = "Public tunnel check failed"
        detail = public.error or "The public camera URL did not respond."
    else:
        overall = "degraded"
        headline = f"Tunnel returned HTTP {public.status or 'unknown'}"
        detail = public.error or public.body_preview or "Review the Diagnostics page."

    return TunnelHealth(
        checked_at=datetime.now(timezone.utc).isoformat(),
        overall=overall,
        headline=headline,
        detail=detail,
        service_installed=service_installed,
        service_running=service_running,
        service_start_mode=start_mode,
        service_pid=pid,
        local_camera=local,
        public_camera=public,
        dashboard=dashboard,
        watchdog_installed=watchdog_installed,
        watchdog_last_action=watchdog_action,
        watchdog_last_check=watchdog_check,
    )
