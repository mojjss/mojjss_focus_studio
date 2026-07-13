# mojjss Focus Studio v5.0

A desktop focus timer with SQLite history, Pixela synchronization, a Cloudflare
Pages dashboard, and an optional live camera route for phones.

## Recommended domain layout

```text
https://timer.mojjss.ir
  Cloudflare Pages dashboard. This stays online even when the desktop is off,
  but its data becomes stale until the desktop reconnects.

https://camera.timer.mojjss.ir
  Cloudflare Tunnel to http://127.0.0.1:8788 on the desktop. This is available
  only while the desktop app and cloudflared service are running.

https://desktop-....ts.net
  Optional Tailscale fallback for the owner PC.
```

The normal Android user does not need Tailscale or Google login. They use the
viewer dashboard key and the separate camera password. The owner uses a
different owner key and receives a larger session history with session notes.

## v5.0 changes

- Desktop Session Log now uses 100-row pages with First, Previous, Next, and
  Last controls.
- Web session history now has Show more / Show fewer controls.
- Separate viewer and owner dashboard keys.
- Owner mode can receive up to 250 recent sessions; viewer mode receives 30 and
  does not receive notes/source fields.
- Default dashboard domain is `timer.mojjss.ir`.
- Optional camera route is `camera.timer.mojjss.ir` through Cloudflare Tunnel.
- Public camera mode no longer needs Tailscale identity headers.
- Five failed camera passwords from one client trigger a 15-minute lockout.
- GitHub-safe repository layout: local config, Pixela token, database, schedule,
  Wrangler secrets, and tunnel credentials are ignored.

## Desktop upgrade

Copy these private files from the existing installation into `desktop_app`:

```text
config.json
focus_history.db
schedule.csv
```

Then run:

```powershell
cd desktop_app
python -m pip install -r requirements.txt
python app.py
```

A fresh installation can copy `schedule.example.csv` to `schedule.csv`, but the
app also creates an empty schedule automatically.

## Cloudflare Pages setup

For the full order, read `SETUP-MOJJSS-IR.md`.

1. Create a D1 database named `focus-studio-dashboard`.
2. Copy `cloudflare_dashboard/wrangler.toml.example` to `wrangler.toml` and put
   the D1 database ID in it.
3. Initialize D1:

```powershell
cd cloudflare_dashboard
npm install
npx wrangler d1 execute focus-studio-dashboard --remote --file=./schema.sql
```

4. Run `GENERATE-AND-SET-SECRETS.bat`, or create three unrelated long random secrets in Cloudflare Pages:

```text
DASHBOARD_VIEWER_KEY   normal user login
DASHBOARD_OWNER_KEY    owner login
DESKTOP_WRITE_KEY      desktop upload only; never share with a viewer
```

5. Deploy with `DEPLOY-TIMER-MOJJSS.bat` or connect this GitHub repository to
   Cloudflare Pages with `cloudflare_dashboard` as the project root.
6. In Pages > Custom domains, attach `timer.mojjss.ir`.
7. In desktop Settings > Cloud dashboard, set:

```text
Dashboard URL: https://timer.mojjss.ir
Desktop write key: the DESKTOP_WRITE_KEY value
```

## Android camera setup

Read `desktop_app/CLOUDFLARE-CAMERA-SETUP.md` and create this Tunnel route:

```text
camera.timer.mojjss.ir -> http://127.0.0.1:8788
```

Recommended desktop settings:

```text
Camera URL: https://camera.timer.mojjss.ir
Allowed origins: https://timer.mojjss.ir, https://camera.timer.mojjss.ir
Require Tailscale identity headers: Off
```

Use a strong camera password. The dashboard access key and camera password
serve different purposes and should not be the same.

## GitHub publishing

Before the first push, verify that these files are absent from `git status`:

```text
desktop_app/config.json
desktop_app/focus_history.db
desktop_app/schedule.csv
cloudflare_dashboard/.dev.vars
cloudflare_dashboard/wrangler.toml
Cloudflare Tunnel token or credential files
```

Pixela credentials can be supplied through `PIXELA_USERNAME`, `PIXELA_TOKEN`,
and `PIXELA_GRAPH_ID` environment variables. Never commit the Pixela token.
