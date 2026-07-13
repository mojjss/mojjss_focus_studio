# v5.2 Cloudflare camera URL fix

This update fixes the desktop settings validator that still required every camera URL to contain `.ts.net`.

The camera URL field now accepts either:

- `https://camera.mojjss.ir` through Cloudflare Tunnel, or
- a private `https://...ts.net` address through Tailscale Serve.

It also updates the dashboard Content Security Policy and camera instructions for `camera.mojjss.ir`.

## Drop-in upgrade

Close the desktop app, replace the files from the v5.2 patch, then start the app again. Keep your private files:

- `desktop_app/config.json`
- `desktop_app/focus_history.db`
- `desktop_app/schedule.csv`

Use these settings:

- Camera URL: `https://camera.mojjss.ir`
- Allowed origins: `https://timer.mojjss.ir, https://camera.mojjss.ir`
- Require Tailscale identity headers: Off
