from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

from config_store import load_config, save_config
from tailscale_tools import (
    TailscaleSetupError,
    configure_serve,
    find_tailscale_cli,
)


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"


def main() -> int:
    print()
    print("mojjss live activity - Tailscale private camera setup")
    print("=" * 60)

    cli = find_tailscale_cli()
    if cli is None:
        print("Tailscale is not installed.")
        print("Opening the official download page...")
        webbrowser.open("https://tailscale.com/download/windows")
        print("Install and sign in, then run this setup again.")
        return 1

    config = load_config(CONFIG_PATH)
    port = int(config.get("tailscale_camera_port", 8788))
    print(f"Local camera server port: {port}")
    print(f"Tailscale CLI: {cli}")
    print()

    try:
        result = configure_serve(port)
    except TailscaleSetupError as exc:
        print(f"Setup failed: {exc}")
        return 1
    except Exception as exc:
        print("Setup crashed unexpectedly.")
        print(type(exc).__name__ + ": " + str(exc))
        print()
        print("Manual recovery:")
        print(f"  tailscale serve reset")
        print(f"  tailscale serve {port}")
        print("  approve the browser consent page if it appears")
        print("  press Ctrl+C after the private URL appears")
        print(f"  tailscale serve --yes --bg http://127.0.0.1:{port}")
        print("  tailscale serve status")
        return 1

    if result.approval_url:
        print("Tailscale needs one-time approval for HTTPS/Serve.")
        print("Opening the approval page...")
        webbrowser.open(result.approval_url)
        print()
        print("Approve it in the browser, then run this setup again.")
        return 2

    config["tailscale_camera_url"] = result.private_url
    save_config(CONFIG_PATH, config)

    print("Success.")
    print(f"Private camera URL: {result.private_url}")
    print()
    print("The URL was saved to config.json.")
    print("Start the desktop app and enable Allow private camera.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
