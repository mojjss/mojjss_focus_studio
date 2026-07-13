from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from config_store import load_config, save_config
from tailscale_tools import (
    TailscaleSetupError,
    configure_serve,
    find_tailscale_cli,
    serve_status_text,
)


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"


def check_local_health(port: int) -> dict:
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise RuntimeError(
            f"The local camera server is not reachable at {url}. "
            "Start the desktop app, keep it open, and run this repair again. "
            f"Details: {exc}"
        ) from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"The local camera server returned invalid health data: {raw[:200]}"
        ) from exc
    if not payload.get("ok"):
        raise RuntimeError(f"The local camera server health check failed: {payload}")
    return payload


def run_cli(cli: Path, args: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    creationflags = 0
    if sys.platform.startswith("win"):
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        [str(cli), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=creationflags,
    )


def main() -> int:
    print()
    print("mojjss private camera - HTTP 502 repair")
    print("=" * 52)

    config = load_config(CONFIG_PATH)
    port = int(config.get("tailscale_camera_port", 8788))

    try:
        health = check_local_health(port)
    except RuntimeError as exc:
        print(f"Local check failed: {exc}")
        return 1

    print(
        "Local camera server is healthy: "
        f"version {health.get('version', '?')} on 127.0.0.1:{port}"
    )

    cli = find_tailscale_cli()
    if cli is None:
        print("Tailscale is not installed or its CLI could not be found.")
        return 1

    print()
    print("Current Tailscale Serve status:")
    print(serve_status_text() or "(no active Serve configuration)")
    print()
    print("This repair resets this computer's current Tailscale Serve mapping")
    print("and points it back to the local camera server.")
    answer = input("Continue? [y/N]: ").strip().lower()
    if answer not in {"y", "yes"}:
        print("Cancelled.")
        return 0

    try:
        reset = run_cli(cli, ["serve", "reset"], timeout=25)
    except subprocess.TimeoutExpired:
        print("Tailscale Serve reset timed out.")
        return 1
    if reset.returncode != 0:
        detail = (reset.stderr or reset.stdout or "Unknown error").strip()
        print(f"Could not reset Tailscale Serve: {detail}")
        return 1

    try:
        result = configure_serve(port)
    except TailscaleSetupError as exc:
        print(f"Could not reconfigure Tailscale Serve: {exc}")
        return 1

    if result.approval_url:
        print("Tailscale needs one-time Serve/HTTPS approval.")
        print(result.approval_url)
        print("Approve it in your browser, then run this repair again.")
        return 2

    config["tailscale_camera_url"] = result.private_url
    save_config(CONFIG_PATH, config)

    print()
    print("Repair completed.")
    print(f"Private camera URL: {result.private_url}")
    print(f"Local target: http://127.0.0.1:{port}")
    print("Open the private URL and try the camera password again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
