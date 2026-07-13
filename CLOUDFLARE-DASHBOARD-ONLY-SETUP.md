# Cloudflare dashboard-only setup (no local npm, no local Wrangler)

This is the recommended setup for networks that cannot reliably reach
`registry.npmjs.org`. The desktop application remains Python. The web project
is deployed by Cloudflare Pages directly from GitHub.

## 1. Create the Pages project

In Cloudflare, use **Workers & Pages -> Create application -> Pages -> Connect
to Git**. Select the GitHub repository and enter:

```text
Project name: focus-studio-dashboard
Production branch: main
Framework preset: None
Build command: exit 0
Build output directory: public
Root directory: cloudflare_dashboard
```

No package installation is required. The `functions/` directory uses only
Cloudflare runtime APIs and local source files.

## 2. Create and initialize D1

Create a D1 database named `focus-studio-dashboard`. Open its **Console**, copy
all of `cloudflare_dashboard/schema.sql`, paste it into the console, and select
**Execute**.

## 3. Bind D1 to Pages

Open the Pages project, then:

```text
Settings -> Bindings -> Add -> D1 database
Variable name: DB
Database: focus-studio-dashboard
```

Save the binding for Production.

## 4. Generate and add the secrets

Run:

```text
cloudflare_dashboard\GENERATE-LOCAL-SECRETS.bat
```

It creates `PRIVATE-CLOUDFLARE-KEYS.txt` locally without npm. In the Pages
project, open:

```text
Settings -> Variables and Secrets -> Add
```

Add each value as an encrypted secret:

```text
DASHBOARD_VIEWER_KEY
DASHBOARD_OWNER_KEY
DESKTOP_WRITE_KEY
```

Never share `DESKTOP_WRITE_KEY` with a viewer.

## 5. Redeploy

After adding the D1 binding and secrets, redeploy the latest commit from the
Pages Deployments page, or push an empty/new commit to GitHub.

## 6. Add the domain

In the Pages project's Custom domains page, add:

```text
timer.mojjss.ir
```

## 7. Configure the desktop app

```text
Dashboard URL: https://timer.mojjss.ir
Desktop write key: DESKTOP_WRITE_KEY
```

## 8. Camera tunnel

Create the Tunnel in the Cloudflare dashboard and publish:

```text
camera.timer.mojjss.ir -> http://127.0.0.1:8788
```

Installing `cloudflared` on Windows is separate from npm and Wrangler.
