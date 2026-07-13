# mojjss Focus Studio

A desktop focus timer and study planner built with Python.

I originally made Focus Studio for my own study and project workflow: start a timed session, keep track of what I am working on, plan the day, and save a clean history that I can search later. The desktop app works on its own, while Pixela sync, the browser dashboard, and remote camera access are optional.

## Features

- Custom focus, short-break, long-break, flow, and personal sessions
- Session title, category, notes, planned duration, start time, and estimated finish time
- One-click copy button for the current activity summary
- Date-based schedule with add, edit, duplicate, and delete controls
- Searchable and editable session history with pagination
- Daily, weekly, category, and activity analytics
- Local SQLite storage with readable CSV copies
- Optional Pixela synchronization for focus minutes
- Optional browser dashboard with separate owner and viewer access
- Optional private camera view through Cloudflare Tunnel
- Configurable themes, timer lengths, categories, sounds, daily goals, and auto-start behavior

## Project structure

```text
desktop_app/
  Python desktop application, local database, timer, schedule, analytics,
  Pixela integration, cloud sync, and optional camera server

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
schedule.csv                Personal schedule
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

The browser dashboard can show the current timer, today’s progress, schedule, analytics, and recent sessions on another computer or phone.

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

- Never commit `config.json`, database files, schedules, exports, private keys, tunnel credentials, or generated secret files.
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
data/
```

## Contributing

Issues, bug reports, and pull requests are welcome. Please avoid including personal schedules, session databases, tokens, passwords, or deployment credentials in screenshots and commits.

## License

This project is available under the MIT License.
