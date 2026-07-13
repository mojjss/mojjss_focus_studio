from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TS_URL_RE = re.compile(r"https://[A-Za-z0-9.-]+\.ts\.net(?::\d+)?(?:/[^\s]*)?")


class TailscaleSetupError(RuntimeError):
    pass


@dataclass
class TailscaleServeResult:
    private_url: str
    output: str
    approval_url: str = ""


def find_tailscale_cli() -> Path | None:
    found = shutil.which("tailscale")
    if found:
        return Path(found)

    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Tailscale"
        / "tailscale.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Tailscale"
        / "tailscale.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _run(
    cli: Path,
    args: list[str],
    *,
    timeout: int = 45,
) -> subprocess.CompletedProcess[str]:
    creationflags = 0
    if os.name == "nt":
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


def _extract_url(text: str) -> str:
    matches = TS_URL_RE.findall(text or "")
    if not matches:
        return ""
    url = matches[0].rstrip("/.,)")
    return url


def _find_dns_name(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("DNSName", "dnsName"):
            dns_name = value.get(key)
            if isinstance(dns_name, str) and dns_name.endswith(".ts.net."):
                return dns_name.rstrip(".")
            if isinstance(dns_name, str) and dns_name.endswith(".ts.net"):
                return dns_name
        for child in value.values():
            found = _find_dns_name(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_dns_name(child)
            if found:
                return found
    return ""


def status_json() -> dict[str, Any]:
    cli = find_tailscale_cli()
    if cli is None:
        raise TailscaleSetupError("Tailscale is not installed.")
    result = _run(cli, ["status", "--json"], timeout=20)
    if result.returncode != 0:
        raise TailscaleSetupError(
            (result.stderr or result.stdout or "Tailscale is not connected.").strip()
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise TailscaleSetupError("Tailscale returned invalid status JSON.") from exc


def current_private_url() -> str:
    try:
        status = status_json()
    except TailscaleSetupError:
        return ""
    dns_name = _find_dns_name(status.get("Self", status))
    return f"https://{dns_name}" if dns_name else ""


def configure_serve(port: int = 8788) -> TailscaleServeResult:
    cli = find_tailscale_cli()
    if cli is None:
        raise TailscaleSetupError(
            "Tailscale is not installed. Install it from the official Tailscale website first."
        )

    try:
        status_result = _run(cli, ["status"], timeout=20)
    except subprocess.TimeoutExpired as exc:
        raise TailscaleSetupError("Tailscale status timed out.") from exc

    if status_result.returncode != 0:
        up_result = _run(cli, ["up"], timeout=60)
        combined = "\n".join(
            part for part in (
                up_result.stdout,
                up_result.stderr,
            )
            if part
        )
        approval = re.search(r"https://login\.tailscale\.com/[^\s]+", combined)
        if approval:
            return TailscaleServeResult(
                private_url="",
                output=combined,
                approval_url=approval.group(0).rstrip(".,)"),
            )
        if up_result.returncode != 0:
            raise TailscaleSetupError(
                combined.strip() or "Could not connect Tailscale."
            )

    # Tailscale Serve can require a one-time HTTPS/Serve consent flow.
    # When launched from Python with captured output on Windows, older/newer
    # Tailscale builds may wait silently instead of showing the consent URL.
    # Use --yes to avoid interactive prompts where possible, and use an
    # explicit localhost URL because Tailscale documents localhost reverse
    # proxy targets as the supported proxy form.
    serve_args_attempts = [
        ["serve", "--yes", "--bg", f"http://127.0.0.1:{int(port)}"],
        ["serve", "--yes", "--bg", str(int(port))],
        ["serve", "--bg", f"http://127.0.0.1:{int(port)}"],
        ["serve", "--bg", str(int(port))],
    ]

    result = None
    combined = ""
    errors: list[str] = []
    for serve_args in serve_args_attempts:
        try:
            result = _run(cli, serve_args, timeout=35)
        except subprocess.TimeoutExpired as exc:
            partial = "\n".join(
                part.decode("utf-8", "replace") if isinstance(part, bytes) else str(part)
                for part in (exc.stdout, exc.stderr)
                if part
            )
            approval = re.search(r"https://login\.tailscale\.com/[^\s]+", partial)
            if approval:
                return TailscaleServeResult(
                    private_url="",
                    output=partial,
                    approval_url=approval.group(0).rstrip(".,)"),
                )
            errors.append(
                f"Command timed out: tailscale {' '.join(serve_args)}"
            )
            continue

        combined = "\n".join(
            part for part in (result.stdout, result.stderr) if part
        )
        approval = re.search(r"https://login\.tailscale\.com/[^\s]+", combined)
        if approval:
            return TailscaleServeResult(
                private_url="",
                output=combined,
                approval_url=approval.group(0).rstrip(".,)"),
            )
        if result.returncode == 0:
            break
        errors.append(
            combined.strip() or f"Command failed: tailscale {' '.join(serve_args)}"
        )
    else:
        manual = (
            "Tailscale Serve did not finish from the automatic setup. This usually means "
            "Serve is waiting for the one-time HTTPS/Serve approval page or the local "
            "Tailscale service is not responding to background Serve configuration.\n\n"
            "Manual fix:\n"
            f"1) Run: tailscale serve reset\n"
            f"2) Run: tailscale serve {int(port)}\n"
            "3) If Tailscale prints or opens an approval link, approve it in the browser.\n"
            "4) Press Ctrl+C after you see the private https://*.ts.net URL.\n"
            f"5) Run: tailscale serve --yes --bg http://127.0.0.1:{int(port)}\n"
            "6) Run: tailscale serve status\n\n"
            + "\n".join(errors[-4:])
        )
        raise TailscaleSetupError(manual)

    if result is None or result.returncode != 0:
        raise TailscaleSetupError(
            combined.strip()
            or "Tailscale Serve could not be configured. Try running the setup as Administrator."
        )

    private_url = _extract_url(combined)
    if not private_url:
        try:
            status = status_json()
            dns_name = _find_dns_name(status.get("Self", status))
            if dns_name:
                private_url = f"https://{dns_name}"
        except TailscaleSetupError:
            pass

    if not private_url:
        status_result = _run(cli, ["serve", "status"], timeout=20)
        private_url = _extract_url(
            "\n".join((status_result.stdout, status_result.stderr))
        )

    if not private_url:
        raise TailscaleSetupError(
            "Serve was configured, but its private HTTPS URL could not be detected. "
            "Run `tailscale serve status` and copy the shown URL into the app settings."
        )

    return TailscaleServeResult(
        private_url=private_url.rstrip("/"),
        output=combined,
    )


def serve_status_text() -> str:
    cli = find_tailscale_cli()
    if cli is None:
        return "Tailscale is not installed."
    result = _run(cli, ["serve", "status"], timeout=20)
    return (result.stdout or result.stderr or "").strip()
