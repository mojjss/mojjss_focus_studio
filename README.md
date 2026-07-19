# mojjss Focus Studio

A desktop focus timer and study planner built with Python.

I originally made Focus Studio for my own study and project workflow: start a session, keep track of what I am working on, plan the day, and keep a history I can review later. The desktop app works on its own. Pixela sync, the browser dashboard, and remote camera access are optional.

## Features

- Focus, short-break, long-break, flow, and personal session modes
- Session title, category, notes, duration, start time, and estimated finish time
- Minimal Focus view for distraction-free sessions
- One-click copy button for the current activity summary
- Date-based schedule with add, edit, duplicate, and delete controls
- Searchable and editable session history
- Analytics by day, weekday, category, and activity
- 1-day, 3-day, 7-day, 30-day, 90-day, and all-time ranges
- Local SQLite storage with readable CSV exports
- Optional Pixela sync for focus minutes
- Optional browser dashboard for viewing and controlling timers from another device
- Two-way desktop/cloud synchronization with offline support
- Optional private camera view through Cloudflare Tunnel
- Configurable themes, timer lengths, categories, sounds, daily goals, and auto-start behavior

## Project structure

```text
desktop_app/
  Main Python application, timer, schedule, database, analytics,
  Pixela integration, cloud sync, local monitor, and camera server

cloudflare_dashboard/
  Browser dashboard, Cloudflare Pages Functions, and D1 database schema
```

The desktop app is the main part of the project. You only need `cloudflare_dashboard/` when you want to host the optional web dashboard.

## Requirements

- Windows 10 or Windows 11
- Python 3.11 or newer
- A webcam only for the optional camera feature
- A Pixela account only for Pixela sync
- A Cloudflare account only for the hosted dashboard or camera tunnel

## Quick start

Clone the repository:

```powershell
git clone https://github.com/mojjss/mojjss_focus_studio.git
cd mojjss_focus_studio\desktop_app
```

Create and activate a virtual environment:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install the dependencies:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Start the app:

```powershell
python app.py
```

Focus Studio creates its local configuration, database, schedule, and timer-state files automatically on first run.

## Using it for your own workflow

You can use the project as a local desktop app without setting up any online service.

A simple setup is:

1. Start the app with `python app.py`.
2. Open **Settings** and adjust the session lengths, categories, sounds, theme, and daily goal.
3. Add the tasks you want to work on from the Timer or Schedule page.
4. Start a session.
5. Review completed work from Sessions and Analytics.

Your data stays on your computer unless you explicitly enable an integration.

### Customizing the app

Most personal settings can be changed inside the application, so editing the source code is not required.

For code-level changes, these are the main files to start with:

```text
desktop_app/app.py
  Main window, pages, timer controls, and application flow

desktop_app/config_store.py
  Default settings and configuration handling

desktop_app/database.py
  Session database and history operations

desktop_app/schedule_store.py
  Schedule storage and editing

desktop_app/charts.py
  Analytics charts

desktop_app/pixela_client.py
  Pixela API integration

desktop_app/cloud_sync.py
desktop_app/cloud_client.py
  Desktop and Cloudflare synchronization

desktop_app/monitor_server.py
  Local browser monitor and private camera API
```

When adapting the project, keep personal credentials and generated data out of source control.

## Local data

Focus Studio stores personal data inside `desktop_app/`:

```text
config.json                 App settings and integration credentials
focus_history.db            SQLite session database
focus_history.db-wal        SQLite working file, when present
focus_history.db-shm        SQLite working file, when present
schedule.csv                Schedule and synchronization metadata
timer_state.json            Restorable active timer state
data/readable/              Human-readable CSV exports
```

These files are excluded by `.gitignore` and should not be committed.

Before moving the app to another computer or replacing your installation, back up:

```text
config.json
focus_history.db
focus_history.db-wal
focus_history.db-shm
schedule.csv
timer_state.json
data/
```

## Pixela sync

Pixela support is optional. Configure it from Settings with:

- Pixela username
- Pixela token
- Graph ID

The same values can be provided through environment variables:

```text
PIXELA_USERNAME
PIXELA_TOKEN
PIXELA_GRAPH_ID
```

Only sessions counted as productive focus time are sent to Pixela.

Keep the Pixela token private. Do not commit it or include it in screenshots.

## Browser dashboard

The optional browser dashboard can show:

- The current timer
- Today’s progress
- Schedule
- Analytics
- Recent sessions

Owner mode can also start and control timers and edit schedule events from another computer or phone.

The dashboard uses three separate keys:

```text
DASHBOARD_VIEWER_KEY   Read-only access
DASHBOARD_OWNER_KEY    Full owner access
DESKTOP_WRITE_KEY      Used by the desktop app for synchronization
```

Anyone using this project should create their own Cloudflare Pages project, D1 database, domain, and secret keys. My personal deployment is not intended to be a shared public service.

### Cloudflare Pages setup

1. Create a Cloudflare Pages project connected to your fork or repository.
2. Set the project root to `cloudflare_dashboard`.
3. Use:

```text
Framework preset: None
Build command: exit 0
Build output directory: public
```

4. Create a Cloudflare D1 database.
5. Run `cloudflare_dashboard/schema.sql` in the D1 console.
6. Add a D1 binding named `DB`.
7. Add the three dashboard keys as encrypted secrets.
8. Redeploy the Pages project.
9. Enter the deployed dashboard URL and `DESKTOP_WRITE_KEY` in the desktop app.

A custom domain is optional. The generated `pages.dev` address also works.

### Synchronization model

Focus Studio uses a local-first desktop app and a cloud-first browser dashboard.

```text
Desktop while offline
  -> saves timers, sessions, and schedule changes locally
  -> uploads them after the connection returns

Dashboard while the desktop is offline
  -> stores timer and schedule changes in D1
  -> the desktop imports them when it reconnects
```

A browser-started timer continues from its stored timestamps even when the desktop is offline. When the desktop reconnects, the same timer appears there.

If separate active timers are created while the devices cannot communicate, the app keeps the data and reports a conflict instead of silently deleting one.

The following still require the desktop computer to be online:

- Camera viewing
- Desktop sounds and notifications
- Immediate local database updates for phone-started sessions
- Pixela uploads after cloud sessions are imported

## Optional private camera

The camera feature lets an approved dashboard user request a short live view from the computer running Focus Studio.

The intended path is:

```text
Browser
  -> HTTPS
Cloudflare
  -> Cloudflare Tunnel
Desktop camera server at http://127.0.0.1:8788
```

The camera server listens on `127.0.0.1`, so it is not directly exposed to the local network. Cloudflare Tunnel creates an outbound connection, so router port forwarding is not required.

Basic setup:

1. Enable the private camera in Settings.
2. Set a strong camera password that is different from the dashboard keys.
3. Create a Cloudflare Tunnel on the computer running the app.
4. Add a published hostname pointing to:

```text
http://127.0.0.1:8788
```

5. Enter the public hostname in the desktop camera URL field.
6. Add the dashboard origin to the allowed-origins field.
7. Test the local health endpoint before testing the public hostname:

```text
http://127.0.0.1:8788/api/health
```

The camera is disabled by default. Do not expose it without a strong password. Cloudflare Access can be added in front of the camera hostname for another layer of authentication.

## Local browser monitor

Focus Studio also includes a small local monitor server, enabled by default on port `8765`.

It is intended for the same computer or a trusted local network. It does not use the cloud dashboard keys.

Disable it from Settings when it is not needed, or set:

```json
{
  "monitor_enabled": false
}
```

Do not expose the local monitor port directly to the public Internet.

## Starting with Windows

You can start the desktop app automatically with Windows Task Scheduler.

```text
Program:
<path-to-python>\pythonw.exe

Arguments:
"<repository-path>\desktop_app\app.py"

Start in:
<repository-path>\desktop_app
```

Use an **At log on** trigger and select **Do not start a new instance** if the task is already running.

## Updating

Pull the latest code and reinstall dependencies when they change:

```powershell
git pull
cd desktop_app
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Back up your local data before moving or replacing an existing installation.

## Security

- Never commit configuration files, databases, schedules, exports, tokens, passwords, or tunnel credentials.
- Use different values for the viewer key, owner key, desktop write key, and camera password.
- Rotate any credential that was accidentally pushed to a public repository.
- Avoid opening the owner dashboard on shared or untrusted devices.
- Check staged files with `git status` before every public push.

## Contributing

Issues, bug reports, and pull requests are welcome.

Please remove personal schedules, session data, tokens, passwords, and deployment credentials from screenshots, logs, and commits.

## License
Copyright © 2026 Mojtaba Sadafi.

This project is available under the MIT License.
See [License](./LICENSE).
