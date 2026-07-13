# Cloud dashboard connection

The desktop app can publish a read-only status snapshot to the Cloudflare Pages
site at `https://timer.mojjss.ir`.

## Connect

1. Open **Settings → Cloud dashboard**.
2. Enable cloud publishing.
3. Set the dashboard URL to `https://timer.mojjss.ir`.
4. Paste the Cloudflare Pages `DESKTOP_WRITE_KEY` secret.
5. Save and test the upload.

The browser uses either `DASHBOARD_VIEWER_KEY` or `DASHBOARD_OWNER_KEY`. Never
reuse or share the desktop write key.

## Uploaded data

The snapshot contains the current activity, timer state, daily summaries,
schedule, recent sessions, Pixela graph identity, and connection status. The
Pixela token and complete SQLite database are never uploaded.
