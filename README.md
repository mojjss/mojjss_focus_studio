# mojjss Focus Studio

A desktop focus timer and study planner built with Python.

Current maintenance package: **v5.6 Stability Update**.

I originally made Focus Studio for my own study and project workflow: start a timed session, keep track of what I am working on, plan the day, and save a clean history that I can search later. The desktop app works on its own, while Pixela sync, the browser dashboard, and remote camera access are optional.

## Features

- Custom focus, short-break, long-break, flow, and personal sessions
- Session title, category, notes, planned duration, start time, and estimated finish time
- One-click copy button for the current activity summary
- Date-based schedule with add, edit, duplicate, and delete controls
- Searchable and editable session history with pagination
- Daily, weekday, category, and activity analytics with 1-day, 3-day, 7-day, 30-day, 90-day, and all-time ranges
- Local SQLite storage with readable CSV copies
- Optional Pixela synchronization for focus minutes
- Optional browser dashboard with separate owner and viewer access
- Start, pause, resume, complete, or cancel a cloud timer from owner mode
- Add and edit schedule events from a phone, even while the desktop is offline
- Two-way local/cloud synchronization with offline desktop support
- Optional private camera view through Cloudflare Tunnel
- Configurable themes, timer lengths, categories, sounds, daily goals, and auto-start behavior
- Built-in Diagnostics page for the local camera, Cloudflare connector, public route, dashboard, and recovery watchdog
- Optional SYSTEM watchdog that can recover a stopped or disconnected `cloudflared` service without resetting v2rayN, Tailscale, adapters, Winsock, or proxy settings
- Desktop notifications when the public tunnel disconnects and when it recovers

## Project structure

```text
desktop_app/
  Python desktop application, local database, timer, schedule, analytics,
  Pixela integration, local-first synchronization, and optional camera server

cloudflare_dashboard/
  Static browser dashboard, Cloudflare Pages Functions, and D1 schema
```

The desktop application is the main part of the project. The Cloudflare folder is only needed when you want to host the browser dashboard yourself.

## Requirements

- Windows 10 or Windows 11
- Python 3.11 or newer recommended
- A webcam only if you plan to use the optional camera feature
- A Cloudflare account only if you plan to use the hosted dashboard or camera tunnel
- A Pixela account only if you plan to use Pixela synchronization

## Quick start

Clone the repository and open the desktop folder:

```powershell
git clone https://github.com/mojjss/mojjss_focus_studio.git
cd mojjss_focus_studio\desktop_app
```

Create a virtual environment:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install the dependencies:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Start the application:

```powershell
python app.py
```

The app creates its local configuration, database, and schedule files automatically on first run.

## Local files

Focus Studio keeps personal data locally inside `desktop_app/`:

```text
config.json                 App settings and integration credentials
focus_history.db            SQLite session database
focus_history.db-wal        SQLite working file, when present
focus_history.db-shm        SQLite working file, when present
schedule.csv                Personal schedule and sync metadata
timer_state.json             Restorable active timer state
data/readable/              Human-readable CSV exports
```

These files are intentionally excluded by `.gitignore`. Do not commit them to a public repository.

The camera password itself is not stored as plain text by the desktop app. A salted password hash is saved in `config.json`.

## Pixela sync

Pixela support is optional. You can configure it from the Settings page with:

- Pixela username
- Pixela token
- Graph ID

The same values can be provided through environment variables:

```text
PIXELA_USERNAME
PIXELA_TOKEN
PIXELA_GRAPH_ID
```

Only sessions marked as productive focus time are sent to Pixela.

## Browser dashboard

The browser dashboard can show the current timer, today’s progress, schedule, analytics, and recent sessions on another computer or phone. Owner mode can also create and control timers and manage schedule events. Recent sessions are shown 10 per page; viewer access can see up to 30 sessions, while owner access can see up to 250.

It supports three separate keys:

```text
DASHBOARD_VIEWER_KEY   Read-only access with a smaller, redacted history
DASHBOARD_OWNER_KEY    Full owner view with a larger history
DESKTOP_WRITE_KEY      Used only by the desktop app to publish updates
```

Each person hosting this project should create their own Cloudflare project, database, domain, and secret keys. The addresses used by my personal deployment are not meant to be shared as a public service.

### Cloudflare Pages setup

1. Create a Cloudflare Pages project connected to this repository.
2. Use `cloudflare_dashboard` as the root directory.
3. Use these build settings:

```text
Framework preset: None
Build command: exit 0
Build output directory: public
```

4. Create a Cloudflare D1 database.
5. Open `cloudflare_dashboard/schema.sql` and run it in the D1 console.
6. Add the D1 binding to the Pages project with the variable name `DB`.
7. Add the three dashboard keys as encrypted secrets.
8. Redeploy the Pages project.
9. Enter your deployed dashboard URL and `DESKTOP_WRITE_KEY` in the desktop app settings.

A custom domain is optional. The generated `pages.dev` address also works.


### How two-way synchronization works

Focus Studio uses a local-first desktop and a cloud-first browser dashboard:

```text
Desktop app while offline
  -> saves timers, sessions, and schedule changes locally
  -> uploads them when the connection returns

Owner dashboard while the laptop is off
  -> stores timer and schedule changes in Cloudflare D1
  -> the desktop imports them when it opens or reconnects
```

A timer started from the browser continues from its stored timestamps; it does not require the laptop to stay online. When the desktop app opens, the same active timer appears there. A timer started offline on the desktop continues locally and is published to the dashboard after reconnection.

If two different active timers are created independently while the devices cannot communicate, neither one is silently deleted. The desktop reports a conflict and preserves the completed sessions so the user can decide which active timer to keep.

The following features require the desktop computer to be online:

- Camera viewing
- Desktop sounds and notifications
- Immediate local SQLite writes for a phone-started session
- Pixela upload, which occurs after the desktop imports the session

## Optional remote camera

The camera feature lets an approved dashboard user request a short live view from the computer running Focus Studio.

The intended setup is:

```text
Browser
  -> HTTPS
Cloudflare
  -> Cloudflare Tunnel
Desktop camera server at http://127.0.0.1:8788
```

The local camera server listens on `127.0.0.1`, so it is not directly exposed to the local network. Cloudflare Tunnel creates an outgoing connection from the computer, which means router port forwarding is not required.

To use it:

1. Enable the private camera in the desktop settings.
2. Set a strong camera password that is different from the dashboard keys.
3. Create a Cloudflare Tunnel on the computer running the app.
4. Add a published hostname that points to:

```text
http://127.0.0.1:8788
```

5. Put that public hostname in the desktop camera URL field.
6. Add the dashboard origin to the allowed-origins field.
7. Restart the desktop app and test `/api/health` locally before testing the public hostname.

The camera is optional and disabled by default. Do not expose it without a strong password. For stricter access control, place Cloudflare Access in front of the camera hostname as an additional identity layer.


### Included Windows maintenance tools

This package includes separate tools for repair and diagnosis:

```text
REPAIR_CLOUDFLARE_TUNNEL.bat
  Reinstalls the remotely managed Cloudflare Tunnel service with the current
  tunnel token, tests TCP port 7844, forces HTTP/2 over IPv4, configures
  automatic service recovery, and verifies the public camera health endpoint.

FOCUS_STUDIO_DIAGNOSIS.bat
  Creates a redacted ZIP report on the Desktop containing app, port, Python,
  JavaScript, SQLite, DNS, proxy, firewall, Cloudflare Tunnel, service,
  connector diagnostics, recent-versus-historical crash events, watchdog,
  and public-endpoint checks. Its summary includes PASS, FAIL, WARNING, INFO,
  a rule-based likely cause, confidence estimate, and suggested fix. It does
  not reset v2rayN, Tailscale, Winsock, network adapters, or Windows proxy.

INSTALL_TUNNEL_WATCHDOG.bat
  Registers a SYSTEM Scheduled Task that checks the route every two minutes.
  It starts a stopped Cloudflared service and restarts it only after repeated
  connector/public-route failures while the local camera origin is healthy.

REMOVE_TUNNEL_WATCHDOG.bat
  Removes the optional automatic-recovery Scheduled Task.

INSTALL_UPDATE_KEEP_DATA.bat
  Copies this release over an existing installation without overwriting
  config.json, the SQLite database, schedule.csv, timer_state.json, exports,
  or readable data.

START_FOCUS_STUDIO.bat
  Starts the app without a console window.

START_FOCUS_STUDIO_DEBUG.bat
  Starts the app with a console window for debugging.
```

For Cloudflare error `1033`, run `REPAIR_CLOUDFLARE_TUNNEL.bat` as Administrator. When prompted, obtain the current tunnel token from:

```text
Cloudflare Dashboard
  -> Networking
  -> Tunnels
  -> mojjss-focus-camera
  -> Add a replica
  -> Windows
```

Copy only the long `eyJ...` value. The token is a secret: never send it in a message, show it in screenshots, or commit it to GitHub. The repair tool accepts it through a hidden prompt and does not place it in its report.

Cloudflare error `1033` means no healthy connector is currently available. A `502` from a Tunnel route is different: the connector can usually reach Cloudflare, but cannot reach the configured local origin. The Diagnostics page and report distinguish these cases before suggesting a repair.

### Stability monitoring in v5.6

The desktop sidebar shows a compact tunnel status and includes a new **Diagnostics** page. The app checks the Cloudflared Windows service, the local camera health endpoint, the public camera route, and the browser dashboard. The monitoring interval and recovery notifications are configurable in Settings.

The desktop monitor is read-only: it never reads the tunnel token and never changes networking. Automatic recovery is separate and optional. Install it with `INSTALL_TUNNEL_WATCHDOG.bat`; its status and log are stored under:

```text
C:\ProgramData\MojjssFocusStudio\
```

The watchdog does not restart Cloudflared merely because the camera app itself is offline. It waits for repeated public-route failures while the local origin is healthy, uses a restart cooldown, and records every action for diagnosis.

Version 5.6 keeps the v5.5.2 fixes for normal browser disconnects, temporary D1 debug logging, and the 1-day/3-day filters. It adds a Diagnostics page, periodic tunnel health monitoring, disconnect/recovery notifications, robust project-path handling in the Windows tools, historical crash-event classification, and an optional automatic tunnel watchdog.

## Local browser monitor

The app also includes a small local monitor server, enabled by default on port `8765`. It is intended for the same computer or a trusted local network and does not use the cloud dashboard keys.

Turn it off in Settings when you do not need it, or set this in `config.json`:

```json
{
  "monitor_enabled": false
}
```

Do not expose the local monitor port directly to the public Internet.

## Starting with Windows

The desktop app can be started automatically with Windows Task Scheduler.

Use:

```text
Program:
<path-to-python>\pythonw.exe

Arguments:
"<repository-path>\desktop_app\app.py"

Start in:
<repository-path>\desktop_app
```

Use an **At log on** trigger and select **Do not start a new instance** if the task is already running.

When Cloudflare Tunnel is used, install `cloudflared` as a Windows service so it can reconnect automatically after startup.

## Security notes

- Never commit `config.json`, `timer_state.json`, database files, schedules, exports, private keys, tunnel credentials, or generated secret files.
- Use different values for the viewer key, owner key, desktop write key, and camera password.
- Rotate a credential immediately if it was ever pushed to a public Git history.
- The hosted camera route is Internet-reachable through Cloudflare, even though the home IP and local port are not directly exposed.
- Browser access keys are kept in browser storage for convenience. Avoid using the dashboard on untrusted or shared devices.
- Review the staged file list with `git status` before every public push.

## Updating

Pull the latest code, reinstall requirements when they change, and start the app normally:

```powershell
git pull
cd desktop_app
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Back up these files before replacing or moving an existing installation:

```text
config.json
focus_history.db
focus_history.db-wal
focus_history.db-shm
schedule.csv
timer_state.json
data/
```

## Contributing

Issues, bug reports, and pull requests are welcome. Please avoid including personal schedules, session databases, tokens, passwords, or deployment credentials in screenshots and commits.

## License

This project is available under the MIT License.
