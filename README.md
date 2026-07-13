# mojjss Focus Studio v5.1 — no local npm required

A Python desktop focus timer with SQLite history, Pixela synchronization, a
Cloudflare Pages dashboard, and an optional Cloudflare Tunnel camera route.

## Recommended addresses

```text
https://timer.mojjss.ir
  Owner/viewer dashboard hosted by Cloudflare Pages.

https://camera.timer.mojjss.ir
  Cloudflare Tunnel to the camera server on the desktop PC.
```

## Deployment model

The website is connected to GitHub through Cloudflare Pages. Every push to the
configured branch triggers a new Pages deployment. Local `npm`, Wrangler, and
`node_modules` are not required for production setup.

The Pages project contains:

```text
cloudflare_dashboard/public/      static HTML, CSS and JavaScript
cloudflare_dashboard/functions/   server-side Pages Functions
cloudflare_dashboard/schema.sql   D1 schema
```

The Functions use only Cloudflare runtime APIs and local modules, so there are
no external JavaScript dependencies.

Read `CLOUDFLARE-DASHBOARD-ONLY-SETUP.md` for the exact setup.

## Access levels

- Viewer key: normal user; smaller/redacted session history.
- Owner key: owner browser/phone; larger history with owner fields.
- Desktop write key: used only by the desktop app to upload snapshots.
- Camera password: separate password for the live camera.

## GitHub safety

Before pushing, verify that these do not appear as staged files:

```text
desktop_app/config.json
desktop_app/focus_history.db
desktop_app/schedule.csv
cloudflare_dashboard/PRIVATE-CLOUDFLARE-KEYS.txt
Cloudflare Tunnel tokens or credential files
```
