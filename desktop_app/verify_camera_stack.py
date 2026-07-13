from __future__ import annotations

import getpass
import json
import platform
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from config_store import load_config
from version import APP_VERSION

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
REPORT_PATH = APP_DIR / "CAMERA_STACK_REPORT.txt"
TIMER_URL = "https://timer.mojjss.ir"
CAMERA_URL = "https://camera.mojjss.ir"
EXPECTED_ORIGIN = TIMER_URL


def redact(text: str) -> str:
    text = re.sub(r"(?i)(--token\s+|service install\s+)(\"?eyJ[A-Za-z0-9._-]+\"?)", r"\1<redacted-token>", text)
    text = re.sub(r"eyJ[A-Za-z0-9._-]{20,}", "<redacted-token>", text)
    return text


def run(command: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if sys.platform.startswith("win")
                else 0
            ),
        )
        output = (result.stdout or "") + (result.stderr or "")
        return result.returncode, redact(output.strip())
    except Exception as exc:
        return 1, f"{type(exc).__name__}: {exc}"


def request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout: int = 15,
    bypass_proxy: bool = False,
) -> tuple[int, dict[str, str], str]:
    req = urllib.request.Request(url, method=method, headers=headers or {})
    opener = (
        urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if bypass_proxy
        else urllib.request.build_opener()
    )
    try:
        with opener.open(req, timeout=timeout) as response:
            return (
                int(response.status),
                dict(response.headers.items()),
                response.read().decode("utf-8", errors="replace"),
            )
    except urllib.error.HTTPError as exc:
        return (
            int(exc.code),
            dict(exc.headers.items()),
            exc.read().decode("utf-8", errors="replace"),
        )


def json_preview(body: str) -> str:
    try:
        value: Any = json.loads(body)
        if isinstance(value, dict):
            for key in list(value):
                if key.lower() in {"token", "password"}:
                    value[key] = "<redacted>"
        return json.dumps(value, ensure_ascii=False, indent=2)[:1500]
    except Exception:
        return body[:700]


def resolve(hostname: str) -> list[str]:
    addresses: set[str] = set()
    for result in socket.getaddrinfo(hostname, None):
        address = result[4][0]
        addresses.add(address)
    return sorted(addresses)


def tcp_test(host: str, port: int, timeout: float = 5.0) -> str:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "PASS"
    except Exception as exc:
        return f"FAIL: {type(exc).__name__}: {exc}"


def windows_listener_info(port: int) -> str:
    if not sys.platform.startswith("win"):
        return "Listener process lookup is Windows-only."
    script = rf"""
$connections = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue
if (-not $connections) {{ Write-Output 'No listening process'; exit 0 }}
foreach ($connection in $connections) {{
  $pidValue = $connection.OwningProcess
  $process = Get-CimInstance Win32_Process -Filter "ProcessId=$pidValue" -ErrorAction SilentlyContinue
  [PSCustomObject]@{{
    LocalAddress = $connection.LocalAddress
    LocalPort = $connection.LocalPort
    PID = $pidValue
    ExecutablePath = $process.ExecutablePath
    CommandLine = $process.CommandLine
  }} | Format-List
}}
"""
    _, output = run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        timeout=20,
    )
    return output or "No output"


def main() -> int:
    lines: list[str] = []
    failures = 0

    def add(text: str = "") -> None:
        lines.append(text)
        print(text)

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        marker = "PASS" if ok else "FAIL"
        add(f"[{marker}] {label}{(': ' + detail) if detail else ''}")
        if not ok:
            failures += 1

    add(f"mojjss Focus Studio camera-stack verification — v{APP_VERSION}")
    add("=" * 72)
    add(f"Time: {datetime.now().astimezone().isoformat(timespec='seconds')}")
    add(f"Project folder: {APP_DIR.parent}")
    add(f"Python: {platform.python_version()} ({sys.executable})")
    add(f"Platform: {platform.platform()}")
    add()

    check("Source version", APP_VERSION == "5.4", APP_VERSION)
    check("Expected app.py exists", (APP_DIR / "app.py").exists(), str(APP_DIR / "app.py"))
    check("Expected camera source exists", (APP_DIR / "tailscale_camera.py").exists(), str(APP_DIR / "tailscale_camera.py"))

    config = load_config(CONFIG_PATH)
    port = int(config.get("tailscale_camera_port", 8788))
    configured_url = str(config.get("tailscale_camera_url", "")).strip().rstrip("/")
    origins = str(config.get("tailscale_camera_allowed_origin", "")).strip()
    require_identity = bool(config.get("tailscale_camera_require_identity", False))
    enabled = bool(config.get("remote_camera_enabled", False))
    password_present = bool(config.get("remote_camera_password_hash")) and bool(
        config.get("remote_camera_password_salt")
    )

    add("Configuration")
    add("-" * 72)
    add(f"Camera enabled: {enabled}")
    add(f"Local port: {port}")
    add(f"Configured camera URL: {configured_url or '(empty)'}")
    add(f"Allowed origins: {origins or '(empty)'}")
    add(f"Require Tailscale identity: {require_identity}")
    add(f"Camera password hash present: {password_present}")
    check("Camera enabled", enabled)
    check("Camera URL", configured_url == CAMERA_URL, configured_url or "empty")
    check("Timer origin allowed", EXPECTED_ORIGIN.lower() in origins.lower(), origins or "empty")
    check("Cloudflare-only identity mode", not require_identity, str(require_identity))
    check("Camera password configured", password_present)
    add()

    add(f"Process listening on port {port}")
    add("-" * 72)
    add(windows_listener_info(port))
    add()

    local_base = f"http://127.0.0.1:{port}"
    add("HTTP tests")
    add("-" * 72)

    local_health: dict[str, Any] = {}
    try:
        status, headers, body = request(local_base + "/api/health", bypass_proxy=True)
        add(f"Local health: HTTP {status}; Server={headers.get('Server', '?')}")
        add(json_preview(body))
        try:
            local_health = json.loads(body)
        except Exception:
            local_health = {}
        check("Local health HTTP 200", status == 200)
        check("Running camera version", local_health.get("version") == APP_VERSION, str(local_health.get("version")))
    except Exception as exc:
        add(f"Local health exception: {type(exc).__name__}: {exc}")
        check("Local health reachable", False)

    try:
        status, headers, body = request(CAMERA_URL + "/api/health")
        add(f"Public camera health: HTTP {status}; Server={headers.get('Server', '?')}")
        add(json_preview(body))
        check("Public camera health HTTP 200", status == 200)
    except Exception as exc:
        add(f"Public camera health exception: {type(exc).__name__}: {exc}")
        check("Public camera health reachable", False)

    try:
        status, headers, body = request(
            CAMERA_URL + "/api/unlock",
            method="OPTIONS",
            headers={
                "Origin": EXPECTED_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-camera-password",
            },
        )
        add(f"Public CORS preflight: HTTP {status}")
        add(f"Access-Control-Allow-Origin: {headers.get('Access-Control-Allow-Origin', '')}")
        check("CORS preflight accepted", status in {200, 204})
        check(
            "CORS allows timer.mojjss.ir",
            headers.get("Access-Control-Allow-Origin", "").rstrip("/").lower()
            == EXPECTED_ORIGIN.lower(),
            headers.get("Access-Control-Allow-Origin", "missing"),
        )
    except Exception as exc:
        add(f"CORS preflight exception: {type(exc).__name__}: {exc}")
        check("CORS preflight reachable", False)

    try:
        status, headers, body = request(TIMER_URL + "/api/health")
        add(f"Timer API health: HTTP {status}")
        add(json_preview(body))
        check("Timer API health HTTP 200", status == 200)
    except Exception as exc:
        add(f"Timer API health exception: {type(exc).__name__}: {exc}")
        check("Timer API health reachable", False)

    try:
        status, _, body = request(TIMER_URL + "/")
        check("Timer website HTTP 200", status == 200)
        check("Timer website is v5.4", "v5.4" in body, "v5.4 marker present" if "v5.4" in body else "marker missing")
    except Exception as exc:
        add(f"Timer page exception: {type(exc).__name__}: {exc}")
        check("Timer page reachable", False)

    add()
    add("DNS and Cloudflare connector tests")
    add("-" * 72)
    for host in ["camera.mojjss.ir", "timer.mojjss.ir", "region1.v2.argotunnel.com"]:
        try:
            addresses = resolve(host)
            add(f"{host}: {', '.join(addresses)}")
            check(f"DNS resolves {host}", bool(addresses))
        except Exception as exc:
            add(f"{host}: FAIL: {type(exc).__name__}: {exc}")
            check(f"DNS resolves {host}", False)

    add(f"TCP region1.v2.argotunnel.com:7844: {tcp_test('region1.v2.argotunnel.com', 7844)}")

    if sys.platform.startswith("win"):
        code, service_status = run(["sc.exe", "query", "Cloudflared"])
        add("Cloudflared service status:")
        add(service_status or "No output")
        check("Cloudflared service query", code == 0)
        check("Cloudflared service running", "RUNNING" in service_status.upper())

        _, service_config = run(["sc.exe", "qc", "Cloudflared"])
        add("Cloudflared service configuration (token redacted):")
        add(service_config or "No output")

        _, srv_output = run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "Resolve-DnsName -Type SRV _v2-origintunneld._tcp.argotunnel.com | Format-Table -AutoSize",
            ]
        )
        add("Cloudflare Tunnel SRV lookup:")
        add(srv_output or "No output")

    add()
    try:
        answer = input("Run password unlock tests too? [y/N]: ").strip().lower()
    except EOFError:
        answer = ""
    if answer == "y":
        password = getpass.getpass("Camera password (not saved): ").strip()
        if password:
            for label, base, bypass in [
                ("Local unlock", local_base, True),
                ("Public unlock", CAMERA_URL, False),
            ]:
                try:
                    status, _, body = request(
                        base + "/api/unlock?verification=1",
                        method="POST",
                        headers={
                            "Origin": EXPECTED_ORIGIN,
                            "X-Camera-Password": password,
                            "Cache-Control": "no-store",
                        },
                        bypass_proxy=bypass,
                    )
                    add(f"{label}: HTTP {status}")
                    add(json_preview(body))
                    check(f"{label} HTTP 200", status == 200)
                except Exception as exc:
                    add(f"{label}: {type(exc).__name__}: {exc}")
                    check(f"{label} reachable", False)

    add()
    add("Summary")
    add("-" * 72)
    add(f"Failures: {failures}")
    if failures == 0:
        add("All core checks passed.")
    else:
        add("At least one check failed. Read the first failed section above; later failures may be consequences of it.")
    add()
    add("Cloudflare route must be exactly:")
    add("  camera.mojjss.ir  ->  http://127.0.0.1:8788")
    add("Cloudflare documents published applications as public-hostname-to-local-service mappings.")

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    add(f"Report saved to: {REPORT_PATH}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
