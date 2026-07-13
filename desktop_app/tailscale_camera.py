from __future__ import annotations

import email.header
import json
import secrets
import sys
import traceback
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from camera_security import has_camera_password, verify_camera_password
from version import APP_VERSION


class TailscaleCameraError(RuntimeError):
    pass


def _decode_identity(value: str) -> str:
    try:
        parts = email.header.decode_header(value)
        result = []
        for chunk, encoding in parts:
            if isinstance(chunk, bytes):
                result.append(chunk.decode(encoding or "utf-8", errors="replace"))
            else:
                result.append(chunk)
        return "".join(result).strip()
    except Exception:
        return value.strip()


def _split_values(value: str | list[str] | tuple[str, ...]) -> set[str]:
    if isinstance(value, (list, tuple)):
        source = "\n".join(str(item) for item in value)
    else:
        source = str(value or "")
    values = set()
    for part in source.replace(",", "\n").splitlines():
        item = part.strip().lower()
        if item:
            values.add(item)
    return values


def test_camera(
    camera_index: int,
    width: int,
    height: int,
) -> tuple[int, int]:
    try:
        import cv2
    except ImportError as exc:
        raise TailscaleCameraError(
            "OpenCV is missing. Run: python -m pip install opencv-python"
        ) from exc

    backend = cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY
    camera = cv2.VideoCapture(int(camera_index), backend)
    if not camera.isOpened() and backend != cv2.CAP_ANY:
        camera.release()
        camera = cv2.VideoCapture(int(camera_index))
    if not camera.isOpened():
        camera.release()
        raise TailscaleCameraError(
            f"Camera {camera_index} could not be opened."
        )

    try:
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, int(width))
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, int(height))
        frame = None
        for _ in range(8):
            ok, candidate = camera.read()
            if ok and candidate is not None:
                frame = candidate
            time.sleep(0.04)
        if frame is None:
            raise TailscaleCameraError("The camera returned no image.")
        actual_height, actual_width = frame.shape[:2]
        return int(actual_width), int(actual_height)
    finally:
        camera.release()


class _CameraCapture:
    def __init__(
        self,
        *,
        camera_index: int,
        width: int,
        height: int,
        fps: int,
        jpeg_quality: int,
        status_callback,
    ) -> None:
        self.camera_index = int(camera_index)
        self.width = max(320, min(1920, int(width)))
        self.height = max(240, min(1080, int(height)))
        self.fps = max(2, min(20, int(fps)))
        self.jpeg_quality = max(40, min(90, int(jpeg_quality)))
        self.status_callback = status_callback

        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._frame: bytes | None = None
        self._sequence = 0
        self._error = ""

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def error(self) -> str:
        with self._lock:
            return self._error

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            self._stop.clear()
            self._error = ""
            self._frame = None
            self._thread = threading.Thread(
                target=self._run,
                name="MojjssTailscaleCameraCapture",
                daemon=True,
            )
            self._thread.start()

    def _set_error(self, message: str) -> None:
        with self._condition:
            self._error = message
            self._condition.notify_all()
        if self.status_callback:
            self.status_callback(f"Private camera: {message}")

    def _run(self) -> None:
        try:
            import cv2
        except ImportError:
            self._set_error(
                "OpenCV is missing. Run: python -m pip install opencv-python"
            )
            return

        backend = cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY
        camera = cv2.VideoCapture(self.camera_index, backend)
        if not camera.isOpened() and backend != cv2.CAP_ANY:
            camera.release()
            camera = cv2.VideoCapture(self.camera_index)
        if not camera.isOpened():
            camera.release()
            self._set_error(f"Camera {self.camera_index} could not be opened.")
            return

        try:
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            camera.set(cv2.CAP_PROP_FPS, self.fps)
            frame_interval = 1.0 / self.fps
            next_frame = time.monotonic()

            for _ in range(5):
                if self._stop.is_set():
                    return
                camera.read()
                time.sleep(0.04)

            while not self._stop.is_set():
                ok, frame = camera.read()
                if not ok or frame is None:
                    self._set_error("The webcam stopped returning frames.")
                    return

                ok, buffer = cv2.imencode(
                    ".jpg",
                    frame,
                    [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality],
                )
                if not ok:
                    self._set_error("JPEG encoding failed.")
                    return

                with self._condition:
                    self._frame = buffer.tobytes()
                    self._sequence += 1
                    self._condition.notify_all()

                next_frame += frame_interval
                delay = next_frame - time.monotonic()
                if delay > 0:
                    self._stop.wait(delay)
                else:
                    next_frame = time.monotonic()
        finally:
            camera.release()
            with self._condition:
                self._condition.notify_all()

    def get_frame(
        self,
        last_sequence: int,
        timeout: float = 3.0,
    ) -> tuple[int, bytes] | None:
        deadline = time.monotonic() + timeout
        with self._condition:
            while (
                self._sequence <= last_sequence
                and not self._stop.is_set()
                and not self._error
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)

            if self._sequence > last_sequence and self._frame is not None:
                return self._sequence, self._frame
            return None

    def stop(self) -> None:
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3)
        with self._lock:
            self._thread = None
            self._frame = None


@dataclass
class _ViewerSession:
    identity: str
    expires_at: float
    last_heartbeat: float


class _CameraHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class TailscaleCameraServer:
    def __init__(
        self,
        *,
        port: int,
        camera_index: int,
        width: int,
        height: int,
        fps: int,
        jpeg_quality: int,
        idle_seconds: int,
        session_minutes: int,
        password_config: dict[str, Any],
        allowed_origins: str | list[str],
        require_identity: bool,
        allowed_users: str | list[str],
        enabled: bool,
        status_callback=None,
    ) -> None:
        self.port = max(1024, min(65535, int(port)))
        self.camera_index = max(0, int(camera_index))
        self.width = max(320, min(1920, int(width)))
        self.height = max(240, min(1080, int(height)))
        self.fps = max(2, min(20, int(fps)))
        self.jpeg_quality = max(40, min(90, int(jpeg_quality)))
        self.idle_seconds = max(8, min(120, int(idle_seconds)))
        self.session_minutes = max(1, min(60, int(session_minutes)))
        self.password_config = dict(password_config)
        self.allowed_origins = {
            item.rstrip("/")
            for item in _split_values(allowed_origins)
        }
        self.require_identity = bool(require_identity)
        self.allowed_users = _split_values(allowed_users)
        self.status_callback = status_callback

        self._enabled = bool(enabled)
        self._lock = threading.RLock()
        self._sessions: dict[str, _ViewerSession] = {}
        self._server: _CameraHttpServer | None = None
        self._server_thread: threading.Thread | None = None
        self._watchdog_stop = threading.Event()
        self._watchdog_thread: threading.Thread | None = None
        self._active_streams = 0
        self._active_identity = ""
        self._unlock_failures: dict[str, list[float]] = {}
        self._unlock_blocked_until: dict[str, float] = {}

        self.capture = _CameraCapture(
            camera_index=self.camera_index,
            width=self.width,
            height=self.height,
            fps=self.fps,
            jpeg_quality=self.jpeg_quality,
            status_callback=status_callback,
        )

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def _status(self, message: str) -> None:
        # Camera requests run in worker threads. A GUI/status callback must
        # never be allowed to abort the HTTP response and turn it into a 502.
        if self.status_callback:
            try:
                self.status_callback(message)
            except Exception:
                traceback.print_exc()

    def _unlock_client_key(self, handler: BaseHTTPRequestHandler) -> str:
        forwarded = str(handler.headers.get("CF-Connecting-IP") or "").strip()
        if forwarded:
            return forwarded
        try:
            return str(handler.client_address[0])
        except Exception:
            return "unknown"

    def unlock_allowed(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            blocked_until = self._unlock_blocked_until.get(key, 0.0)
            if blocked_until > now:
                return False, max(1, int(blocked_until - now))
            if blocked_until:
                self._unlock_blocked_until.pop(key, None)
            recent = [
                stamp for stamp in self._unlock_failures.get(key, [])
                if now - stamp <= 600
            ]
            self._unlock_failures[key] = recent
            return True, 0

    def record_unlock_failure(self, key: str) -> int:
        now = time.monotonic()
        with self._lock:
            recent = [
                stamp for stamp in self._unlock_failures.get(key, [])
                if now - stamp <= 600
            ]
            recent.append(now)
            self._unlock_failures[key] = recent
            if len(recent) >= 5:
                self._unlock_blocked_until[key] = now + 900
                self._unlock_failures[key] = []
                return 900
        return 0

    def clear_unlock_failures(self, key: str) -> None:
        with self._lock:
            self._unlock_failures.pop(key, None)
            self._unlock_blocked_until.pop(key, None)

    def start(self) -> None:
        if self._server is not None:
            return

        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = f"MojjssPrivateCamera/{APP_VERSION}"
            # HTTP/1.0 is deliberate here. The MJPEG response has no fixed
            # Content-Length, and Python requires one for every HTTP/1.1
            # response. Closing each response is more reliable behind Serve.
            protocol_version = "HTTP/1.0"

            def log_message(self, _format, *_args) -> None:
                return

            def _origin(self) -> str:
                return str(self.headers.get("Origin") or "").rstrip("/")

            def _cors_allowed(self) -> bool:
                origin = self._origin()
                if not origin:
                    return True
                return origin.lower() in owner.allowed_origins

            def _common_headers(self) -> None:
                origin = self._origin()
                if origin and origin.lower() in owner.allowed_origins:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                self.send_header(
                    "Access-Control-Allow-Methods",
                    "GET, POST, OPTIONS",
                )
                self.send_header(
                    "Access-Control-Allow-Headers",
                    "Content-Type, X-Camera-Token, X-Camera-Password",
                )
                self.send_header(
                    "Access-Control-Allow-Private-Network",
                    "true",
                )
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Connection", "close")

            def _json(
                self,
                status: int,
                payload: dict[str, Any],
            ) -> None:
                body = json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                try:
                    self.close_connection = True
                    self.send_response(status)
                    self._common_headers()
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    self.wfile.flush()
                except (
                    BrokenPipeError,
                    ConnectionResetError,
                    ConnectionAbortedError,
                    OSError,
                ):
                    pass

            def _safe_exception_json(self, exc: BaseException) -> None:
                message = str(exc) or exc.__class__.__name__
                owner._status(
                    "Private camera: request failed · " + message[:140]
                )
                try:
                    self._json(
                        500,
                        {
                            "ok": False,
                            "error": "Private camera server error.",
                            "detail": message[:500],
                            "type": exc.__class__.__name__,
                        },
                    )
                except Exception:
                    traceback.print_exc()

            def _read_body(self, limit: int = 64_000) -> bytes:
                transfer_encoding = str(
                    self.headers.get("Transfer-Encoding") or ""
                ).lower()
                if "chunked" in transfer_encoding:
                    chunks: list[bytes] = []
                    total = 0
                    while True:
                        line = self.rfile.readline(128)
                        if not line:
                            raise ValueError("Request body ended before the chunk size.")
                        try:
                            size = int(line.split(b";", 1)[0].strip(), 16)
                        except ValueError as exc:
                            raise ValueError("Invalid chunked request body.") from exc
                        if size == 0:
                            # Consume optional trailer headers and the final blank line.
                            while True:
                                trailer = self.rfile.readline(8192)
                                if trailer in (b"", b"\r\n", b"\n"):
                                    break
                            break
                        total += size
                        if total > limit:
                            raise ValueError("Request body is too large.")
                        chunk = self.rfile.read(size)
                        if len(chunk) != size:
                            raise ValueError("Incomplete chunked request body.")
                        chunks.append(chunk)
                        if self.rfile.read(2) != b"\r\n":
                            raise ValueError("Invalid chunk separator.")
                    return b"".join(chunks)

                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError as exc:
                    raise ValueError("Invalid Content-Length header.") from exc
                if length <= 0:
                    return b""
                if length > limit:
                    raise ValueError("Request body is too large.")
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("Incomplete request body.")
                return raw

            def _read_json(self) -> dict[str, Any]:
                raw = self._read_body()
                if not raw:
                    return {}
                try:
                    value = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError("Request body is not valid JSON.") from exc
                if not isinstance(value, dict):
                    raise ValueError("JSON request body must be an object.")
                return value

            def _identity(self) -> str | None:
                raw = str(self.headers.get("Tailscale-User-Login") or "").strip()
                if raw:
                    return _decode_identity(raw).lower()
                if owner.require_identity:
                    return None
                access_email = str(
                    self.headers.get("Cf-Access-Authenticated-User-Email") or ""
                ).strip().lower()
                return access_email or "cloudflare-viewer"

            def _authorized_identity(self) -> str | None:
                identity = self._identity()
                if not identity:
                    self._json(
                        403,
                        {
                            "ok": False,
                            "error": (
                                "Required proxy identity is missing. Open this URL "
                                "through Tailscale Serve, or disable the identity "
                                "requirement for the Cloudflare Tunnel route."
                            ),
                        },
                    )
                    return None
                if owner.require_identity and owner.allowed_users and identity not in owner.allowed_users:
                    self._json(
                        403,
                        {
                            "ok": False,
                            "error": "This proxy identity is not allowed.",
                            "identity": identity,
                        },
                    )
                    return None
                return identity

            def _token(self) -> str:
                query = parse_qs(urlparse(self.path).query)
                return (
                    str(self.headers.get("X-Camera-Token") or "").strip()
                    or str(query.get("token", [""])[0]).strip()
                )

            def do_OPTIONS(self) -> None:
                self.close_connection = True
                if not self._cors_allowed():
                    self.send_response(403)
                    self.send_header("Content-Length", "0")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    return
                self.send_response(204)
                self._common_headers()
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self) -> None:
                try:
                    parsed = urlparse(self.path)
                    path = parsed.path

                    if path == "/favicon.ico":
                        self.close_connection = True
                        self.send_response(204)
                        self._common_headers()
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return

                    if not self._cors_allowed():
                        self._json(403, {"ok": False, "error": "Origin is not allowed."})
                        return

                    if path == "/api/health":
                        self._json(
                            200,
                            {
                                "ok": True,
                                "service": "mojjss-private-camera",
                                "version": APP_VERSION,
                                "enabled": owner.enabled,
                            },
                        )
                        return

                    if path in {"/", "/viewer"}:
                        identity = self._authorized_identity()
                        if not identity:
                            return
                        body = owner.viewer_html(identity).encode("utf-8")
                        self.close_connection = True
                        self.send_response(200)
                        self._common_headers()
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        self.wfile.flush()
                        return

                    if path == "/api/status":
                        identity = self._authorized_identity()
                        if not identity:
                            return
                        self._json(200, owner.status_payload(identity))
                        return

                    if path == "/camera/stream":
                        identity = self._authorized_identity()
                        if not identity:
                            return
                        token = self._token()
                        if not owner.validate_token(token, identity):
                            self._json(401, {"ok": False, "error": "Invalid or expired camera token."})
                            return
                        owner.stream_mjpeg(self, token, identity)
                        return

                    self._json(404, {"ok": False, "error": "Not found."})

                except Exception as exc:
                    traceback.print_exc()
                    self._safe_exception_json(exc)
            def do_POST(self) -> None:
                try:
                    parsed = urlparse(self.path)
                    path = parsed.path

                    if not self._cors_allowed():
                        self._json(403, {"ok": False, "error": "Origin is not allowed."})
                        return

                    identity = self._authorized_identity()
                    if not identity:
                        return

                    if path == "/api/unlock":
                        client_key = owner._unlock_client_key(self)
                        allowed, retry_after = owner.unlock_allowed(client_key)
                        if not allowed:
                            self.send_response(429)
                            self._common_headers()
                            body = json.dumps(
                                {
                                    "ok": False,
                                    "error": "Too many incorrect passwords. Try again later.",
                                    "retry_after_seconds": retry_after,
                                },
                                separators=(",", ":"),
                            ).encode("utf-8")
                            self.send_header("Content-Type", "application/json; charset=utf-8")
                            self.send_header("Retry-After", str(retry_after))
                            self.send_header("Content-Length", str(len(body)))
                            self.send_header("Connection", "close")
                            self.end_headers()
                            self.wfile.write(body)
                            return
                        if not owner.enabled:
                            self._json(
                                409,
                                {"ok": False, "error": "Camera is disabled by the desktop owner."},
                            )
                            return
                        if not has_camera_password(owner.password_config):
                            self._json(
                                409,
                                {"ok": False, "error": "Camera password is not configured."},
                            )
                            return
                        # Prefer a header-only unlock request. Reverse proxies can
                        # re-frame browser request bodies while forwarding them to
                        # this local HTTP/1.x server. Avoiding a body removes that
                        # fragile boundary. JSON remains accepted for compatibility.
                        password = str(
                            self.headers.get("X-Camera-Password") or ""
                        )
                        if not password:
                            try:
                                payload = self._read_json()
                            except ValueError as exc:
                                self._json(
                                    400,
                                    {
                                        "ok": False,
                                        "error": "Invalid unlock request.",
                                        "detail": str(exc),
                                    },
                                )
                                return
                            password = str(payload.get("password") or "")
                        if not verify_camera_password(password, owner.password_config):
                            blocked_for = owner.record_unlock_failure(client_key)
                            payload = {
                                "ok": False,
                                "error": "Incorrect camera viewer password.",
                            }
                            if blocked_for:
                                payload["error"] = (
                                    "Too many incorrect passwords. Access is locked for 15 minutes."
                                )
                                payload["retry_after_seconds"] = blocked_for
                            self._json(401 if not blocked_for else 429, payload)
                            return
                        owner.clear_unlock_failures(client_key)
                        token = owner.issue_token(identity)
                        self._json(
                            200,
                            {
                                "ok": True,
                                "token": token,
                                "identity": identity,
                                "expires_seconds": owner.session_minutes * 60,
                                "stream_path": "/camera/stream",
                            },
                        )
                        return

                    if path == "/api/heartbeat":
                        token = self._token()
                        if not owner.touch_token(token, identity):
                            self._json(
                                401,
                                {"ok": False, "error": "Camera session expired."},
                            )
                            return
                        self._json(200, {"ok": True})
                        return

                    if path == "/api/stop":
                        token = self._token()
                        owner.stop_token(token, identity)
                        self._json(200, {"ok": True})
                        return

                    self._json(404, {"ok": False, "error": "Not found."})

                except Exception as exc:
                    traceback.print_exc()
                    self._safe_exception_json(exc)


        try:
            self._server = _CameraHttpServer(("127.0.0.1", self.port), Handler)
        except OSError as exc:
            raise TailscaleCameraError(
                f"Could not start the private camera server on port {self.port}: {exc}"
            ) from exc

        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            name="MojjssTailscaleCameraHttp",
            daemon=True,
        )
        self._server_thread.start()

        self._watchdog_stop.clear()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog,
            name="MojjssTailscaleCameraWatchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

        state = "enabled" if self.enabled else "disabled"
        self._status(
            f"Private camera: local server ready · 127.0.0.1:{self.port} · {state}"
        )

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)
            if not self._enabled:
                self._sessions.clear()
                self._active_identity = ""
        if not enabled:
            self.capture.stop()
            self._status("Private camera: disabled")
        else:
            self._status("Private camera: enabled · waiting for viewer")

    def issue_token(self, identity: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            # One viewer at a time. Issuing a new token revokes old sessions.
            self._sessions.clear()
            self._sessions[token] = _ViewerSession(
                identity=identity,
                expires_at=now + self.session_minutes * 60,
                last_heartbeat=now,
            )
            self._active_identity = identity
        self._status(f"Private camera: unlocked for {identity}")
        return token

    def validate_token(self, token: str, identity: str) -> bool:
        if not token or not self.enabled:
            return False
        now = time.monotonic()
        with self._lock:
            session = self._sessions.get(token)
            if not session:
                return False
            if session.identity != identity:
                return False
            if now >= session.expires_at:
                self._sessions.pop(token, None)
                return False
            if now - session.last_heartbeat > self.idle_seconds:
                self._sessions.pop(token, None)
                return False
            return True

    def touch_token(self, token: str, identity: str) -> bool:
        if not self.validate_token(token, identity):
            return False
        with self._lock:
            session = self._sessions.get(token)
            if not session:
                return False
            session.last_heartbeat = time.monotonic()
        return True

    def stop_token(self, token: str, identity: str) -> None:
        with self._lock:
            session = self._sessions.get(token)
            if session and session.identity == identity:
                self._sessions.pop(token, None)
            no_sessions = not self._sessions
            if no_sessions:
                self._active_identity = ""
        if no_sessions:
            self.capture.stop()
            self._status("Private camera: enabled · viewer disconnected")

    def _watchdog(self) -> None:
        while not self._watchdog_stop.wait(1):
            now = time.monotonic()
            removed = False
            with self._lock:
                for token, session in list(self._sessions.items()):
                    if (
                        now >= session.expires_at
                        or now - session.last_heartbeat > self.idle_seconds
                    ):
                        self._sessions.pop(token, None)
                        removed = True
                no_sessions = not self._sessions
                if no_sessions:
                    self._active_identity = ""
            if no_sessions and self.capture.running:
                self.capture.stop()
                if removed:
                    self._status(
                        "Private camera: viewer heartbeat expired · webcam released"
                    )

    def status_payload(self, identity: str) -> dict[str, Any]:
        with self._lock:
            active = len(self._sessions)
            active_identity = self._active_identity
        return {
            "ok": True,
            "service": "mojjss-private-camera",
            "version": APP_VERSION,
            "enabled": self.enabled,
            "password_protected": has_camera_password(self.password_config),
            "identity": identity,
            "viewer_active": bool(active),
            "active_identity": active_identity,
            "camera_running": self.capture.running,
            "video": {
                "width": self.width,
                "height": self.height,
                "fps": self.fps,
                "format": "multipart-mjpeg",
                "audio": False,
            },
        }

    def stream_mjpeg(
        self,
        handler: BaseHTTPRequestHandler,
        token: str,
        identity: str,
    ) -> None:
        self.capture.start()
        with self._lock:
            self._active_streams += 1
            self._active_identity = identity
        self._status(f"Private camera: streaming to {identity}")

        handler.send_response(200)
        handler._common_headers()  # type: ignore[attr-defined]
        handler.send_header(
            "Content-Type",
            "multipart/x-mixed-replace; boundary=frame",
        )
        handler.send_header("Cross-Origin-Resource-Policy", "cross-origin")
        handler.end_headers()

        sequence = 0
        try:
            while self.validate_token(token, identity):
                result = self.capture.get_frame(sequence, timeout=3)
                if result is None:
                    if self.capture.error:
                        break
                    continue
                sequence, frame = result
                header = (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
                )
                handler.wfile.write(header)
                handler.wfile.write(frame)
                handler.wfile.write(b"\r\n")
                handler.wfile.flush()
        except (
            BrokenPipeError,
            ConnectionResetError,
            ConnectionAbortedError,
            OSError,
        ):
            pass
        finally:
            with self._lock:
                self._active_streams = max(0, self._active_streams - 1)
                no_streams = self._active_streams == 0
            if no_streams:
                self.capture.stop()
            self._status("Private camera: stream ended · webcam released")

    def viewer_html(self, identity: str) -> str:
        safe_identity = (
            identity.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>mojjss private camera</title>
<style>
:root{{color-scheme:dark;background:#07101c;color:#eaf2ff;font-family:system-ui,sans-serif}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:18px}}
main{{width:min(960px,100%);background:#111b29;border:1px solid #26364b;border-radius:18px;padding:18px;box-sizing:border-box}}
h1{{margin:0 0 6px;font-size:24px}}p{{color:#9db0c8}}
form{{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0}}
input,button{{border-radius:10px;padding:11px 12px;border:1px solid #34465e;background:#0a1421;color:#eaf2ff}}
input{{flex:1;min-width:230px}}button{{background:#4cc2ff;color:#06111d;font-weight:800;cursor:pointer}}
img{{width:100%;min-height:280px;object-fit:contain;background:#02060c;border-radius:14px;display:none}}
.status{{margin:10px 0;color:#9db0c8}}.live{{color:#40d9a0}}.error{{color:#ff7a8a}}
</style>
</head>
<body>
<main>
<h1>mojjss private camera</h1>
<p>Viewer identity: <strong>{safe_identity}</strong></p>
<form id="form">
<input id="password" type="password" autocomplete="current-password" placeholder="Camera viewer password">
<button id="button" type="submit">Start camera</button>
</form>
<div id="status" class="status">Camera is stopped.</div>
<img id="stream" alt="Private live camera">
</main>
<script>
let token="";
let timer=null;
const status=document.getElementById("status");
const stream=document.getElementById("stream");
const button=document.getElementById("button");
async function requestJson(path,options={{}},attempt=0){{
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),12000);
  let response;
  try{{
    response=await fetch(path,{{...options,signal:controller.signal}});
  }}catch(error){{
    clearTimeout(timeout);
    if(attempt<1){{
      await new Promise(resolve=>setTimeout(resolve,500));
      return requestJson(path,options,attempt+1);
    }}
    throw new Error(path+" could not reach the desktop camera server: "+String(error.message||error));
  }}
  clearTimeout(timeout);
  const contentType=(response.headers.get("Content-Type")||"").toLowerCase();
  const raw=await response.text();
  let data={{}};
  if(raw){{
    try{{data=JSON.parse(raw);}}
    catch(_error){{
      const preview=raw.replace(/\\s+/g," ").slice(0,180);
      throw new Error(path+" returned non-JSON content (HTTP "+response.status+", "+(contentType||"no content-type")+"): "+preview);
    }}
  }}else{{
    throw new Error(path+" returned an empty response (HTTP "+response.status+"). Check the desktop app console/status.");
  }}
  if(!response.ok)throw new Error(data.detail||data.error||("HTTP "+response.status));
  return data;
}}
async function stop(){{
  if(timer)clearInterval(timer);timer=null;
  if(token){{
    fetch("/api/stop?token="+encodeURIComponent(token),{{method:"POST",keepalive:true}}).catch(()=>{{}});
  }}
  token="";
  stream.src="";
  stream.style.display="none";
  button.textContent="Start camera";
  status.textContent="Camera is stopped.";
  status.className="status";
}}
document.getElementById("form").addEventListener("submit",async(e)=>{{
  e.preventDefault();
  if(token){{await stop();return;}}
  const password=document.getElementById("password").value;
  status.textContent="Unlocking…";
  try{{
    const data=await requestJson("/api/unlock",{{
      method:"POST",
      headers:{{"X-Camera-Password":password}}
    }});
    token=data.token;
    document.getElementById("password").value="";
    stream.src="/camera/stream?token="+encodeURIComponent(token)+"&cb="+Date.now();
    stream.style.display="block";
    button.textContent="Stop camera";
    status.textContent="Private camera is live.";
    status.className="status live";
    timer=setInterval(()=>requestJson("/api/heartbeat?token="+encodeURIComponent(token),{{method:"POST"}}).catch(stop),4000);
  }}catch(error){{
    status.textContent=String(error.message||error);
    status.className="status error";
  }}
}});
document.addEventListener("visibilitychange",()=>{{if(document.hidden)stop();}});
window.addEventListener("pagehide",stop);
</script>
</body>
</html>"""

    def stop(self) -> None:
        self.set_enabled(False)
        self._watchdog_stop.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=2)
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=2)
        self.capture.stop()
