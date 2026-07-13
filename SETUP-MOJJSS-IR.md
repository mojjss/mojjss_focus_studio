# Setup order for `mojjss.ir` — dashboard-only method

1. Wait until `mojjss.ir` is **Active** in Cloudflare DNS.
2. Push this repository to GitHub. Confirm private desktop data and key files
   are ignored.
3. Create a Cloudflare **Pages** Git project with:

```text
Root directory: cloudflare_dashboard
Build command: exit 0
Build output directory: public
```

4. Create D1 database `focus-studio-dashboard`; open its Console, paste all of
   `cloudflare_dashboard/schema.sql`, and execute it.
5. Bind that database to the Pages project with variable name `DB`.
6. Run `cloudflare_dashboard/GENERATE-LOCAL-SECRETS.bat`, then add all three
   generated values in Pages -> Settings -> Variables and Secrets as encrypted
   secrets.
7. Redeploy the Pages project and attach `timer.mojjss.ir` as its custom domain.
8. Configure the Python desktop app with the dashboard URL and
   `DESKTOP_WRITE_KEY`.
9. Create a Cloudflare Tunnel route:

```text
camera.timer.mojjss.ir -> http://127.0.0.1:8788
```

10. Configure the camera URL/origins in the desktop app and keep Tailscale
identity checking off for the Cloudflare camera route.

Detailed instructions are in `CLOUDFLARE-DASHBOARD-ONLY-SETUP.md`.
