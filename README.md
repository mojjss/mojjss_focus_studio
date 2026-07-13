# mojjss Focus Studio v5.4

A Python desktop focus timer with SQLite history, Pixela synchronization, a Cloudflare Pages dashboard, and an optional live camera published through Cloudflare Tunnel.

This file replaces all other Markdown documentation in the v5.4 package.

## Current deployment

```text
Dashboard:  https://timer.mojjss.ir
Camera:     https://camera.mojjss.ir
Local API:  http://127.0.0.1:8788
```

The dashboard is hosted by Cloudflare Pages. The camera stays on the laptop and is reached through an outbound Cloudflare Tunnel connection:

```text
Browser
  -> HTTPS to Cloudflare
  -> Cloudflare Tunnel
  -> http://127.0.0.1:8788 on the laptop
```

No router port forwarding is required, and the laptop's home IP is not exposed as the camera origin. The dashboard and camera are separate services: the dashboard can remain online when the laptop is off, but live data becomes stale and the camera becomes unavailable.

## Security review of the v5.4 package

### Verdict

No real Cloudflare token, Pixela token, dashboard key, camera password, private key, email address, or other live credential was embedded in the v5.4 full replacement ZIP that was reviewed. The package is safe to publish as source code **after correcting the `.gitignore` issue described below and confirming no private files were already committed in Git history**.

The current setup is reasonably protected for personal use, but it is not a high-assurance authentication system. The most important remaining issues are the unauthenticated LAN dashboard, a missing ignore rule for readable CSV copies, and the fact that the public camera relies mainly on one shared password.

### What is already done well

- The camera HTTP server binds only to `127.0.0.1`, not directly to the LAN or Internet.
- Cloudflare Tunnel uses an outbound connector; no inbound router port is opened.
- Camera passwords are salted and stored as PBKDF2-HMAC-SHA256 verifiers, not as plaintext.
- Password checks use constant-time comparison.
- Camera session tokens are generated with Python's cryptographically secure `secrets` module.
- Camera tokens expire, heartbeat inactivity releases the webcam, and issuing a new token revokes the previous viewer.
- Five incorrect camera-password attempts from one client address cause a 15-minute lockout.
- The Cloudflare dashboard uses separate viewer, owner, and desktop-write keys.
- The secret generator creates three unrelated 256-bit random values.
- Cloudflare API responses use `Cache-Control: no-store`.
- The web dashboard has a restrictive Content Security Policy and escapes dynamic task, note, category, and schedule text before placing it into HTML.
- D1 writes use prepared statements rather than string-built SQL.
- Viewer mode removes session notes and source fields.
- GitHub Actions has read-only repository permissions.
- The main private files are already listed in `.gitignore`.

### Important issue 1: local dashboard is exposed to the LAN without authentication

`desktop_app/monitor_server.py` defaults to:

```python
host="0.0.0.0"
```

and the feature is enabled by default. This exposes `/`, `/api/status`, the schedule, current activity, recent sessions, and Pixela information to devices that can reach port `8765` on the laptop. There is no password on this local server.

This does not affect `timer.mojjss.ir` or the Cloudflare camera. The safest no-break action is to disable the old local dashboard when it is not needed:

```json
"monitor_enabled": false
```

in `desktop_app/config.json`.

A later code cleanup should instead bind it to `127.0.0.1` by default. Do not publish port `8765` through a tunnel or router.

### Important issue 2: `.gitignore` misses the actual readable-data folder

The application writes automatic readable copies here:

```text
desktop_app/data/readable/focus_sessions.csv
desktop_app/data/readable/sync_log.csv
```

but the supplied `.gitignore` ignores `desktop_app/readable_data/`, which is a different path. This can accidentally publish session history.

Add these rules to the root `.gitignore`:

```gitignore
# Actual automatic readable-data directory
desktop_app/data/

# Generated reports and local backups
_backup_before_v54_*/
desktop_app/CAMERA_STACK_REPORT.txt
desktop_app/PRIVATE_CAMERA_DIAGNOSTIC.txt

# Local app data and secrets
desktop_app/config.json
desktop_app/focus_history.db
desktop_app/focus_history.db-*
desktop_app/schedule.csv
desktop_app/exports/
desktop_app/backups/
desktop_app/*.log

# Cloudflare local secrets and credentials
cloudflare_dashboard/node_modules/
cloudflare_dashboard/.wrangler/
cloudflare_dashboard/.dev.vars
cloudflare_dashboard/wrangler.toml
cloudflare_dashboard/.secrets*.json
cloudflare_dashboard/*PRIVATE*.txt
*.pem
*.crt
*.key
*.json.credentials
cert.pem

# Python, editors, and OS files
__pycache__/
*.py[cod]
.venv/
venv/
.vscode/
.idea/
.DS_Store
Thumbs.db
```

The same `data/` rule should also be added to `desktop_app/.gitignore`:

```gitignore
data/
```

### Important issue 3: the public camera uses shared-password authentication

A Cloudflare published application is publicly reachable unless Cloudflare Access or another identity layer protects it. The current camera has meaningful application-level protections, but anyone can reach the password prompt and attempt logins.

Current protections:

- strong camera password required;
- per-IP lockout;
- short-lived random viewing token;
- one active viewer;
- webcam stops after heartbeat loss.

Limitations:

- there is no required Google/email identity in the current Cloudflare route;
- distributed attempts from many IP addresses can bypass a per-IP limit;
- anyone who learns the shared password can view the camera until the password is changed.

For stronger security later, place `camera.mojjss.ir` behind Cloudflare Access and allow only approved accounts. Test this on a separate hostname or Access policy before changing production so the current browser flow is not interrupted.

### Medium issue: dashboard key is stored persistently in `localStorage`

The viewer or owner key is stored as:

```text
focusDashboardReadKey
```

in browser `localStorage`. It persists after the browser is closed and can be read by JavaScript running under the same origin. This is convenient, but less safe on shared computers or if the site ever develops an XSS vulnerability.

Safer behavior for later:

- use `sessionStorage` instead of `localStorage`; or
- use an HttpOnly, Secure, SameSite cookie issued by a server-side login flow; or
- use Cloudflare Access for browser authentication.

For the current version, use the viewer key rather than the owner key on other people's devices and press **Log out** afterward.

### Medium issue: camera password is stored in `sessionStorage`

After one successful camera login, the plaintext camera password is retained in the current browser tab as:

```text
mojjssPrivateCameraPassword
```

It is removed when the tab session ends, but JavaScript on the same origin can read it while the tab is open. Close the tab after use. A future version should retain only the short-lived camera token, not the original password.

### Medium issue: camera token appears in the stream URL

The MJPEG image uses a URL similar to:

```text
https://camera.mojjss.ir/camera/stream?token=...
```

Tokens in URLs may appear in intermediary request logs and diagnostic tools. The token is random and short-lived, and `Referrer-Policy: no-referrer` reduces leakage, but a cookie or authorization header would be a cleaner design. This is not an emergency because the token expires and is bound to the current camera session.

### Medium issue: camera password work factor is lower than current OWASP guidance

The package uses PBKDF2-HMAC-SHA256 with 100,000 iterations. That is substantially better than plaintext or a fast hash, but current OWASP guidance recommends a higher work factor for PBKDF2-HMAC-SHA256. A future version should migrate existing password verifiers to a stronger work factor or Argon2id after testing startup and unlock performance on the laptop.

Changing the iteration count directly in the current code would invalidate compatibility with existing password data, so do not edit it casually in the working v5.4 installation.

### Lower-risk observations

- `/api/health` is public and reveals the service name, version, and enabled state.
- `/api/status` on the camera can reveal whether the camera is enabled or active before a password is entered.
- `config.json` stores the Pixela token and desktop-write key as plaintext on the local filesystem. It is ignored by Git but not encrypted at rest. Protect the Windows account and laptop disk; a future version could use Windows DPAPI or Credential Manager.
- Python dependencies use minimum versions (`>=`) rather than exact versions and hashes. This is convenient but not fully reproducible. A public release should add a tested lock file or fully pinned requirements.
- GitHub Actions uses `actions/checkout@v4`, `actions/setup-python@v5`, and `actions/setup-node@v4`. These are reputable official actions, but full commit-SHA pinning is stronger supply-chain protection.
- The full replacement installer force-stops any process listening on port `8788`. Use it only when installing over an existing copy. `START-V54-CLEAN.bat` is safer because it checks whether the process looks like the timer before stopping it.
- The hardcoded domains `timer.mojjss.ir` and `camera.mojjss.ir` are public configuration, not secrets. They make the repository less reusable, but do not expose a credential.

## Required private files

Never commit or upload these:

```text
desktop_app/config.json
desktop_app/focus_history.db
desktop_app/focus_history.db-wal
desktop_app/focus_history.db-shm
desktop_app/schedule.csv
desktop_app/data/
desktop_app/exports/
desktop_app/backups/
cloudflare_dashboard/PRIVATE-CLOUDFLARE-KEYS.txt
cloudflare_dashboard/.dev.vars
cloudflare_dashboard/wrangler.toml
CAMERA_STACK_REPORT.txt
PRIVATE_CAMERA_DIAGNOSTIC.txt
Cloudflare Tunnel tokens
Cloudflare credential JSON files
Pixela tokens
private keys and certificates
_backup_before_v54_*/
```

Before every public push:

```powershell
git status
git diff --cached --name-only

git ls-files | Select-String -Pattern `
  "config\.json|focus_history\.db|schedule\.csv|desktop_app/data/|PRIVATE-CLOUDFLARE|CAMERA_STACK_REPORT|PRIVATE_CAMERA_DIAGNOSTIC|_backup_before|json\.credentials|cert\.pem"
```

The expected config file in Git is only:

```text
desktop_app/config.example.json
```

If a live secret was ever committed, deleting it in a later commit is not enough. Rotate the secret first and remove it from Git history.

## Installation

```powershell
cd "D:\my_projects\mojjss_focus_studio\desktop_app"
python -m pip install -r requirements.txt
python app.py
```

Keep these private files when upgrading:

```text
desktop_app/config.json
desktop_app/focus_history.db
desktop_app/focus_history.db-wal
desktop_app/focus_history.db-shm
desktop_app/schedule.csv
```

## Cloudflare Pages dashboard setup

Create a Pages project connected to the Git repository:

```text
Project name: focus-studio-dashboard
Production branch: main
Framework preset: None
Build command: exit 0
Build output directory: public
Root directory: cloudflare_dashboard
```

Create a D1 database named:

```text
focus-studio-dashboard
```

Paste and execute:

```text
cloudflare_dashboard/schema.sql
```

Bind the database to the Pages project as:

```text
Variable name: DB
```

Run:

```text
cloudflare_dashboard\GENERATE-LOCAL-SECRETS.bat
```

Add the three generated values to Pages as encrypted secrets:

```text
DASHBOARD_VIEWER_KEY
DASHBOARD_OWNER_KEY
DESKTOP_WRITE_KEY
```

Attach the custom domain:

```text
https://timer.mojjss.ir
```

Configure the desktop app:

```text
Cloud dashboard enabled: On
Dashboard URL: https://timer.mojjss.ir
Desktop write key: DESKTOP_WRITE_KEY
```

The owner browser uses `DASHBOARD_OWNER_KEY`; normal users use `DASHBOARD_VIEWER_KEY`. Never give the desktop-write key to a browser user.

## Cloudflare camera setup

Create a Cloudflare Tunnel route:

```text
camera.mojjss.ir -> http://127.0.0.1:8788
```

Configure the desktop app:

```text
Allow private camera: On
Camera URL: https://camera.mojjss.ir
Local camera server port: 8788
Allowed origins: https://timer.mojjss.ir, https://camera.mojjss.ir
Require Tailscale identity headers: Off
```

Use a strong camera password that is different from all dashboard keys.

Test:

```powershell
curl.exe --noproxy "*" "http://127.0.0.1:8788/api/health"
curl.exe --connect-timeout 15 "https://camera.mojjss.ir/api/health"
Get-Service Cloudflared
```

Expected camera response:

```json
{"ok":true,"service":"mojjss-private-camera","version":"5.4","enabled":true}
```

## Starting automatically after Windows login

### Cloudflare Tunnel

Run once in Administrator PowerShell:

```powershell
sc.exe config Cloudflared start= delayed-auto
sc.exe failure Cloudflared reset= 86400 actions= restart/60000/restart/60000/restart/60000
sc.exe failureflag Cloudflared 1
Start-Service Cloudflared
```

### Desktop app

Create a Task Scheduler task:

```text
Name: Mojjss Focus Studio
Trigger: At log on
Delay: 30 seconds
Run only when the user is logged on
Program:
C:\Users\Moj Sadafi\AppData\Local\Programs\Python\Python311\pythonw.exe
Arguments:
"D:\my_projects\mojjss_focus_studio\desktop_app\app.py"
Start in:
D:\my_projects\mojjss_focus_studio\desktop_app
If already running: Do not start a new instance
```

After reboot:

```powershell
Get-Service Cloudflared
curl.exe --connect-timeout 15 "https://camera.mojjss.ir/api/health"
Get-Process pythonw,python -ErrorAction SilentlyContinue
```

## Files that must remain

These are runtime or deployment files. Do not remove them merely because their names mention Tailscale or an older architecture:

```text
LICENSE
.gitignore
desktop_app/.gitignore
.github/workflows/quality-checks.yml

desktop_app/app.py
desktop_app/version.py
desktop_app/tailscale_camera.py
desktop_app/tailscale_tools.py
desktop_app/camera_security.py
desktop_app/monitor_server.py
desktop_app/cloud_client.py
desktop_app/config_store.py
desktop_app/database.py
desktop_app/schedule_store.py
desktop_app/pixela_client.py
desktop_app/network_http.py
desktop_app/charts.py
desktop_app/csv_tools.py
desktop_app/requirements.txt
desktop_app/config.example.json
desktop_app/schedule.example.csv

cloudflare_dashboard/public/
cloudflare_dashboard/functions/
cloudflare_dashboard/schema.sql
cloudflare_dashboard/GENERATE-LOCAL-SECRETS.bat
cloudflare_dashboard/GENERATE-LOCAL-SECRETS.ps1
```

`tailscale_camera.py` is a misleading legacy filename, but it now contains the active Cloudflare-compatible camera server. Removing it breaks the app. `tailscale_tools.py` is imported by `app.py`; removing it also breaks startup.

## Markdown files safe to remove after replacing the root README

After placing this file at the repository root as `README.md`, all of these duplicate Markdown files can be deleted:

```text
README-FIRST.md
README-v54.md
FULL-REPLACEMENT-README.md
SECURITY.md
SETUP-MOJJSS-IR.md
CLOUDFLARE-DASHBOARD-ONLY-SETUP.md

desktop_app/README.md
desktop_app/README-CLOUD.md
desktop_app/CLOUDFLARE-CAMERA-SETUP.md
desktop_app/TAILSCALE-SERVE-TIMEOUT-FIX.md

cloudflare_dashboard/OWNER-SETUP.md
cloudflare_dashboard/VIEWER-SETUP.md
```

PowerShell cleanup command:

```powershell
Remove-Item -Force `
  .\README-FIRST.md, `
  .\README-v54.md, `
  .\FULL-REPLACEMENT-README.md, `
  .\SECURITY.md, `
  .\SETUP-MOJJSS-IR.md, `
  .\CLOUDFLARE-DASHBOARD-ONLY-SETUP.md, `
  .\desktop_app\README.md, `
  .\desktop_app\README-CLOUD.md, `
  .\desktop_app\CLOUDFLARE-CAMERA-SETUP.md, `
  .\desktop_app\TAILSCALE-SERVE-TIMEOUT-FIX.md, `
  .\cloudflare_dashboard\OWNER-SETUP.md, `
  .\cloudflare_dashboard\VIEWER-SETUP.md
```

## Optional utility files safe to remove from your installed copy

These are not required for normal runtime. Keep them in a reusable public project only when you want other people to have setup and repair helpers.

### Old Tailscale-only setup and repair helpers

Safe to remove if you will not use Tailscale fallback:

```text
desktop_app/SETUP-TAILSCALE-CAMERA.bat
desktop_app/setup_tailscale_camera.py
desktop_app/FIX-TAILSCALE-SERVE-TIMEOUT.bat
desktop_app/REPAIR-PRIVATE-CAMERA-502.bat
desktop_app/repair_tailscale_camera_502.py
```

Do **not** remove `tailscale_camera.py` or `tailscale_tools.py`.

### Diagnostics

Safe to remove after the system is stable:

```text
desktop_app/DIAGNOSE-PRIVATE-CAMERA.bat
desktop_app/diagnose_private_camera.py
desktop_app/VERIFY-CAMERA-STACK.bat
desktop_app/verify_camera_stack.py
```

### One-time replacement installer

Safe to remove after v5.4 is installed and verified:

```text
INSTALL-OVER-EXISTING-PROJECT.bat
INSTALL-OVER-EXISTING-PROJECT.ps1
```

### Duplicate launch helpers

`run_web_dashboard.bat` is effectively another `python app.py` launcher and can be removed. Keep `run_windows.bat` as the simple launcher.

```text
desktop_app/run_web_dashboard.bat
```

`open_dashboard_only.bat` is optional convenience and can be removed if unused:

```text
desktop_app/open_dashboard_only.bat
```

`START-V54-CLEAN.bat` and `stop_old_camera_listener.ps1` are optional recovery helpers. They are not required when Task Scheduler starts `app.py` normally, but they are useful if an old process occupies port `8788`.

`INSTALL-CLOUDFLARED-SERVICE.bat` can be removed from your installed copy after the Windows service is working, but it is useful in a public self-hosting repository.

## Files that can be deleted automatically after generation

These generated files are private or temporary and should never be committed:

```text
_backup_before_v54_*/
desktop_app/CAMERA_STACK_REPORT.txt
desktop_app/PRIVATE_CAMERA_DIAGNOSTIC.txt
desktop_app/__pycache__/
cloudflare_dashboard/PRIVATE-CLOUDFLARE-KEYS.txt
```

Keep a private backup of `PRIVATE-CLOUDFLARE-KEYS.txt` outside the repository before deleting the repository copy.

## Public reuse by other people

Other users can clone the source, but they need their own:

```text
Cloudflare account
Pages project
D1 database
viewer, owner, and desktop-write keys
domain or Pages hostname
Cloudflare Tunnel and camera hostname
camera password
Pixela account/token if Pixela is enabled
```

They must replace the hardcoded `timer.mojjss.ir` and `camera.mojjss.ir` values with their own hostnames. They must not connect their desktop app to your Pages database, write key, or camera route.

## Official security references

- Cloudflare Tunnel published applications: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/
- Cloudflare Access for self-hosted applications: https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/
- OWASP Password Storage Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- OWASP HTML5 Security Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/HTML5_Security_Cheat_Sheet.html
- OWASP REST Security Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html
- GitHub secret-removal guidance: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository
- pip secure installs: https://pip.pypa.io/en/stable/topics/secure-installs/
- GitHub Actions secure use: https://docs.github.com/en/actions/reference/security/secure-use
