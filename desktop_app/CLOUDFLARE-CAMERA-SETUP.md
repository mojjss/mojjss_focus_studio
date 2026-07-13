# Cloudflare camera route for Android

This route removes Tailscale from the phone. The phone opens a normal HTTPS
site and enters the separate camera password.

## Cloudflare dashboard

1. Open **Zero Trust → Networks → Tunnels**.
2. Create a tunnel named `mojjss-focus-camera`.
3. Choose the Windows connector and install `cloudflared` on the desktop PC.
4. Add a published application route:

```text
Hostname: camera.timer.mojjss.ir
Service:  http://127.0.0.1:8788
```

5. Copy the Windows tunnel token.
6. Run `INSTALL-CLOUDFLARED-SERVICE.bat` as Administrator and paste the token.

## Desktop app settings

```text
Allow private camera: On
Camera public/private URL: https://camera.timer.mojjss.ir
Local camera server port: 8788
Allowed website origins:
https://timer.mojjss.ir, https://camera.timer.mojjss.ir
Require Tailscale identity headers: Off
Allowed Tailscale users: leave blank
```

Set a strong camera password, then save and test the local camera.

## Security

The camera server still binds only to `127.0.0.1`. Cloudflare Tunnel is the
only public route. A successful viewer needs the camera password; five failed
passwords from one client address trigger a 15-minute lockout. Viewing tokens
are short-lived, only one viewer is active at a time, and the webcam is released
when the heartbeat stops.
