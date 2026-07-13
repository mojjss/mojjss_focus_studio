# Owner setup — `timer.mojjss.ir`

The owner and normal viewer use the same website, but sign in with different
keys.

## Owner access

Open:

```text
https://timer.mojjss.ir
```

Enter `DASHBOARD_OWNER_KEY`. Owner mode receives the larger recent-session
history and may display session notes/source fields.

## Required Cloudflare Pages secrets

Configure these three unrelated random values in the Pages project:

```text
DASHBOARD_OWNER_KEY
DASHBOARD_VIEWER_KEY
DESKTOP_WRITE_KEY
```

Do not put any of them in Git or in `wrangler.toml`.

## Desktop connection

In the desktop app, enable cloud publishing and set:

```text
Dashboard URL: https://timer.mojjss.ir
Desktop write key: the DESKTOP_WRITE_KEY value
```

## Camera

Publish this Cloudflare Tunnel route:

```text
camera.timer.mojjss.ir -> http://127.0.0.1:8788
```

In desktop camera settings use:

```text
Camera URL: https://camera.timer.mojjss.ir
Allowed origins: https://timer.mojjss.ir, https://camera.timer.mojjss.ir
Require Tailscale identity headers: Off
```

Keep a separate strong camera password. The camera is available only while the
desktop app and `cloudflared` are running.
