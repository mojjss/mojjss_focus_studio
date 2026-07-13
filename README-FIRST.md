# Read this first

This is the v5.0 architecture update for `timer.mojjss.ir`.

1. Preserve `desktop_app/config.json`, `focus_history.db`, and `schedule.csv`.
2. Replace the application files and start `desktop_app/app.py`.
3. Deploy `cloudflare_dashboard` to Cloudflare Pages and attach
   `timer.mojjss.ir`.
4. Create separate viewer, owner, and desktop-write secrets.
5. For Android camera access, create the Cloudflare Tunnel hostname
   `camera.timer.mojjss.ir` and disable the Tailscale identity requirement.

The Tailscale route can remain as an owner-only fallback on the PC.
