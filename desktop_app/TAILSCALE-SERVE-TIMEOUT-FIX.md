# Tailscale Serve timeout fix

Your automatic setup timed out while running:

```text
tailscale serve --bg 8788
```

This usually happens on first Serve setup when Tailscale is waiting for the one-time HTTPS/Serve consent page, but the Python wrapper captured the output so the approval URL was not visible.

## Manual commands

Open PowerShell and run:

```powershell
cd "D:\my_projects\Mini Learning Projects\focus app\pixela_focus_timer 3.0"

& "C:\Program Files\Tailscale\tailscale.exe" status
& "C:\Program Files\Tailscale\tailscale.exe" serve reset
& "C:\Program Files\Tailscale\tailscale.exe" serve 8788
```

If a browser approval page opens, approve it.

When you see a URL like:

```text
https://your-laptop.your-tailnet.ts.net
```

press `Ctrl+C`, then run:

```powershell
& "C:\Program Files\Tailscale\tailscale.exe" serve --yes --bg http://127.0.0.1:8788
& "C:\Program Files\Tailscale\tailscale.exe" serve status
```

Copy the `https://...ts.net` URL into the desktop app Settings → Private camera over Tailscale → Tailscale camera URL.

The v4.1 script tries the explicit `http://127.0.0.1:8788` target and shows these manual steps instead of crashing when Serve times out.
