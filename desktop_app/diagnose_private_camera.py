from __future__ import annotations

import getpass
import json
import platform
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from config_store import load_config
from tailscale_tools import find_tailscale_cli
from version import APP_VERSION

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
REPORT_PATH = APP_DIR / "PRIVATE_CAMERA_DIAGNOSTIC.txt"


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("<redacted>" if key.lower() in {"token", "password"} else _redact_payload(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    return value


def _request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout: int = 12,
    direct: bool = False,
) -> tuple[int, dict[str, str], str]:
    request = urllib.request.Request(url, method=method, headers=headers or {})
    # Localhost must bypass proxies. Public Cloudflare/Tailscale routes use the
    # normal system proxy settings so the diagnostic matches the browser.
    opener = (
        urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if direct
        else urllib.request.build_opener()
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return int(response.status), dict(response.headers.items()), body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return int(exc.code), dict(exc.headers.items()), body


def _json_preview(body: str) -> str:
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        return body[:500]
    return json.dumps(_redact_payload(value), ensure_ascii=False, indent=2)[:1200]


def _run_tailscale(args: list[str]) -> str:
    cli = find_tailscale_cli()
    if cli is None:
        return "Tailscale CLI not found."
    try:
        result = subprocess.run(
            [str(cli), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0,
        )
        return (result.stdout or result.stderr or "").strip()
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def main() -> int:
    lines: list[str] = []

    def add(text: str = "") -> None:
        lines.append(text)
        print(text)

    add(f"mojjss private camera diagnostic — v{APP_VERSION}")
    add("=" * 58)
    add(f"Time: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    add(f"Python: {platform.python_version()}")
    add(f"Windows/platform: {platform.platform()}")

    config = load_config(CONFIG_PATH)
    port = int(config.get("tailscale_camera_port", 8788))
    private_url = str(config.get("tailscale_camera_url", "")).strip().rstrip("/")
    allowed_users = str(config.get("tailscale_camera_allowed_users", "")).strip()
    identity = next((item.strip() for item in allowed_users.split(",") if item.strip()), "viewer@example.com")

    add(f"Local port: {port}")
    add(f"Private URL: {private_url or '(not configured)'}")
    add(f"Test identity for localhost: {identity}")
    add()
    add("Tailscale status:")
    add(_run_tailscale(["status"]) or "(empty)")
    add()
    add("Tailscale Serve status:")
    add(_run_tailscale(["serve", "status"]) or "(empty)")

    local_base = f"http://127.0.0.1:{port}"
    add()
    add("1) Local health")
    try:
        status, headers, body = _request(local_base + "/api/health", direct=True)
        add(f"HTTP {status}; server={headers.get('Server', '?')}")
        add(_json_preview(body))
    except Exception as exc:
        add(f"FAILED: {type(exc).__name__}: {exc}")
        REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
        add(f"Report saved to: {REPORT_PATH}")
        return 1

    add()
    password = getpass.getpass("Camera viewer password (not saved in report): ").strip()
    if not password:
        add("Password test skipped.")
    else:
        add("2) Local header-only unlock")
        try:
            status, headers, body = _request(
                local_base + "/api/unlock",
                method="POST",
                headers={
                    "Tailscale-User-Login": identity,
                    "X-Camera-Password": password,
                    "Cache-Control": "no-store",
                },
                direct=True,
            )
            add(f"HTTP {status}; server={headers.get('Server', '?')}")
            add(_json_preview(body))
        except Exception as exc:
            add(f"FAILED: {type(exc).__name__}: {exc}")

        if private_url:
            add()
            add("3) Configured camera route header-only unlock")
            try:
                status, headers, body = _request(
                    private_url + "/api/unlock?diagnostic=1",
                    method="POST",
                    headers={
                        "X-Camera-Password": password,
                        "Cache-Control": "no-store",
                    },
                )
                add(f"HTTP {status}; server={headers.get('Server', '?')}")
                add(_json_preview(body))
            except Exception as exc:
                add(f"FAILED: {type(exc).__name__}: {exc}")

            add()
            add("4) Browser-origin unlock through configured route")
            try:
                status, headers, body = _request(
                    private_url + "/api/unlock?diagnostic=browser-origin",
                    method="POST",
                    headers={
                        "Origin": private_url.rstrip("/"),
                        "X-Camera-Password": password,
                        "Cache-Control": "no-store",
                    },
                )
                add(f"HTTP {status}; server={headers.get('Server', '?')}")
                add(_json_preview(body))
            except Exception as exc:
                add(f"FAILED: {type(exc).__name__}: {exc}")

    add()
    add("Interpretation:")
    add("- Local health failure: the desktop camera server is not running/listening.")
    add("- Local unlock succeeds but the configured route fails: Tunnel/Serve or network path issue.")
    add("- Route unlock succeeds but browser-origin unlock fails: CORS/origin allow-list issue.")
    add("- All unlock tests succeed: the browser camera route is ready.")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    add(f"Report saved to: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
