# mojjss Focus Studio — Desktop app v5.0

This app includes the focus timer, paginated session history, analytics,
schedule editor, Pixela synchronization, Cloudflare dashboard publishing, and
an optional live camera route.

## Install

```powershell
python -m pip install -r requirements.txt
python app.py
```

Keep these private files when upgrading:

```text
config.json
focus_history.db
schedule.csv
```

## Cloud dashboard

Set the dashboard URL to `https://timer.mojjss.ir` and use the private
`DESKTOP_WRITE_KEY` configured in Cloudflare Pages. See `README-CLOUD.md`.

## Android-friendly camera route

Create a Cloudflare Tunnel published-application route:

```text
camera.timer.mojjss.ir -> http://127.0.0.1:8788
```

Then configure:

```text
Camera URL: https://camera.timer.mojjss.ir
Allowed origins: https://timer.mojjss.ir, https://camera.timer.mojjss.ir
Require Tailscale identity headers: Off
```

Set a strong, separate camera password. Five failed attempts from the same
client within ten minutes trigger a fifteen-minute lockout. The local service
still binds only to `127.0.0.1`; Cloudflare Tunnel carries the live request to
that local service without opening an inbound router port.

## Optional Tailscale fallback

The existing Tailscale setup scripts remain available for an owner-only private
route. Enable the Tailscale identity requirement only for that route.

## Camera cleanup

The browser sends a heartbeat while viewing. Closing or hiding the page stops
the heartbeat and sends a stop request. The desktop releases the webcam after
the configured timeout, which defaults to 15 seconds.
