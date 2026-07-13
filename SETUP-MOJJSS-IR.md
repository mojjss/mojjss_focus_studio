# Setup order for `mojjss.ir`

## 1. Activate the domain in Cloudflare DNS

Add `mojjss.ir` to Cloudflare, choose the Free plan, copy the two assigned
nameservers, and set those nameservers at the `.ir` registrar. Continue only
after Cloudflare reports the zone as **Active**.

## 2. Put this repository on GitHub

The repository is prepared so local secrets, the Pixela token, SQLite data,
Wrangler secrets, and Tunnel credentials are ignored. Before pushing, run:

```powershell
git status --ignored
```

Confirm that `desktop_app/config.json`, `focus_history.db`, `schedule.csv`,
`cloudflare_dashboard/wrangler.toml`, and any private key files are not staged.

## 3. Create the dashboard

In Cloudflare Pages, create or connect a project named
`focus-studio-dashboard` with `cloudflare_dashboard` as the project root.
Attach this custom domain:

```text
timer.mojjss.ir
```

Create a D1 database named `focus-studio-dashboard`, copy
`wrangler.toml.example` to `wrangler.toml`, insert the D1 database ID, and run:

```powershell
cd cloudflare_dashboard
npm install
npx wrangler d1 execute focus-studio-dashboard --remote --file=./schema.sql
```

Run `GENERATE-AND-SET-SECRETS.bat`, then deploy with
`DEPLOY-TIMER-MOJJSS.bat`.

## 4. Connect the desktop snapshot

In the desktop app:

```text
Dashboard URL: https://timer.mojjss.ir
Desktop write key: DESKTOP_WRITE_KEY from PRIVATE-CLOUDFLARE-KEYS.txt
```

Enable cloud publishing and test the upload.

## 5. Create the camera route

Create a Cloudflare Tunnel published application:

```text
camera.timer.mojjss.ir -> http://127.0.0.1:8788
```

Install `cloudflared` as a Windows service using the command shown by
Cloudflare or `desktop_app/INSTALL-CLOUDFLARED-SERVICE.bat`.

Configure the desktop camera:

```text
Camera URL: https://camera.timer.mojjss.ir
Allowed origins: https://timer.mojjss.ir, https://camera.timer.mojjss.ir
Require Tailscale identity headers: Off
```

Set a strong camera password and enable the camera.

## 6. Daily use

- Normal viewer: `https://timer.mojjss.ir` + viewer key + camera password.
- Owner: the same URL + owner key + camera password.
- The dashboard stays online when the PC is off, but its snapshot is stale.
- The live camera works only while the desktop app and `cloudflared` are online.
