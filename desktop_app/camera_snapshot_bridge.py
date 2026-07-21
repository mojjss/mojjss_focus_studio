from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import requests

from camera_security import has_camera_password
from network_http import ReliableHttpClient, concise_network_error


PROOF_PREFIX = "focus-studio-camera-photo-v1:"


class SnapshotCameraError(RuntimeError):
    pass


def _decode_urlsafe(value: str) -> bytes:
    text = str(value or "").strip()
    padding = "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


class SnapshotCameraBridge:
    """Poll the Pages/D1 snapshot queue and upload password-approved JPEGs."""

    def __init__(
        self,
        *,
        base_url: str,
        write_key: str,
        password_config: dict[str, Any],
        camera_index: int = 0,
        width: int = 960,
        height: int = 540,
        jpeg_quality: int = 72,
        poll_seconds: int = 2,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.write_key = str(write_key or "").strip()
        self.password_config = password_config
        self.camera_index = int(camera_index)
        self.width = max(320, min(1920, int(width)))
        self.height = max(240, min(1080, int(height)))
        self.jpeg_quality = max(35, min(90, int(jpeg_quality)))
        self.poll_seconds = max(1, min(15, int(poll_seconds)))
        self.status_callback = status_callback
        self.http = ReliableHttpClient(
            user_agent="Mojjss-Focus-Studio-Snapshot/1.0",
            retries=3,
            connect_timeout=10,
            read_timeout=35,
        )
        self._stopped = threading.Event()
        self._thread = threading.Thread(
            target=self._worker,
            name="focus-studio-snapshot-camera",
            daemon=True,
        )

    @property
    def configured(self) -> bool:
        return bool(
            self.base_url.startswith("https://")
            and self.write_key
            and has_camera_password(self.password_config)
        )

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        if self._thread.is_alive():
            self._thread.join(timeout=4)
        self.http.close()

    def _status(self, message: str) -> None:
        if self.status_callback is not None:
            self.status_callback(message)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout=None,
    ) -> dict[str, Any]:
        headers = {
            "X-Write-Key": self.write_key,
            "Authorization": f"Bearer {self.write_key}",
        }
        kwargs: dict[str, Any] = {"headers": headers}
        if payload is not None:
            headers["Content-Type"] = "application/json"
            kwargs["data"] = json.dumps(
                payload, separators=(",", ":")
            ).encode("utf-8")
        response = self.http.request(
            method,
            f"{self.base_url}{path}",
            timeout=timeout,
            **kwargs,
        )
        try:
            data = response.json() if response.text else {}
        except ValueError as exc:
            raise SnapshotCameraError(
                "The photo API returned invalid JSON; redeploy the dashboard."
            ) from exc
        if not response.ok:
            raise SnapshotCameraError(
                str(data.get("error") or f"HTTP {response.status_code}")
            )
        return data

    def _valid_proof(self, request_id: str, supplied: str) -> bool:
        try:
            key = base64.b64decode(
                str(self.password_config["remote_camera_password_hash"]),
                validate=True,
            )
            supplied_bytes = _decode_urlsafe(supplied)
        except Exception:
            return False
        expected = hmac.new(
            key,
            (PROOF_PREFIX + request_id).encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return hmac.compare_digest(expected, supplied_bytes)

    def _capture_jpeg(self) -> tuple[bytes, int, int]:
        try:
            import cv2
        except ImportError as exc:
            raise SnapshotCameraError(
                "OpenCV is missing. Run: python -m pip install opencv-python"
            ) from exc

        backend = cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY
        camera = cv2.VideoCapture(self.camera_index, backend)
        if not camera.isOpened() and backend != cv2.CAP_ANY:
            camera.release()
            camera = cv2.VideoCapture(self.camera_index)
        if not camera.isOpened():
            camera.release()
            raise SnapshotCameraError(
                f"Camera {self.camera_index} could not be opened."
            )

        try:
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            frame = None
            for _ in range(7):
                ok, candidate = camera.read()
                if ok and candidate is not None:
                    frame = candidate
                time.sleep(0.06)
            if frame is None:
                raise SnapshotCameraError("The camera returned no image.")

            actual_height, actual_width = frame.shape[:2]
            encoded: bytes | None = None
            quality = self.jpeg_quality
            while quality >= 35:
                ok, buffer = cv2.imencode(
                    ".jpg",
                    frame,
                    [int(cv2.IMWRITE_JPEG_QUALITY), quality],
                )
                if not ok:
                    raise SnapshotCameraError("JPEG encoding failed.")
                candidate = buffer.tobytes()
                if len(candidate) <= 650_000:
                    encoded = candidate
                    break
                quality -= 8
            if encoded is None:
                raise SnapshotCameraError("The captured frame is too large.")
            return encoded, int(actual_width), int(actual_height)
        finally:
            camera.release()

    def _finish_error(self, request_id: str, message: str) -> None:
        try:
            self._request(
                "POST",
                "/api/camera/frame",
                {"request_id": request_id, "error": message[:300]},
                timeout=(8, 20),
            )
        except Exception:
            pass

    def _handle(self, request_id: str, proof: str) -> None:
        if not self._valid_proof(request_id, proof):
            self._finish_error(request_id, "Incorrect camera password.")
            self._status("Camera photos: rejected incorrect password")
            return

        self._status("Camera photos: capturing requested frame…")
        try:
            jpeg, width, height = self._capture_jpeg()
            result = self._request(
                "POST",
                "/api/camera/frame",
                {
                    "request_id": request_id,
                    "image_base64": base64.b64encode(jpeg).decode("ascii"),
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "width": width,
                    "height": height,
                    "bytes": len(jpeg),
                },
                timeout=(12, 60),
            )
            if not result.get("ok"):
                raise SnapshotCameraError(
                    str(result.get("error") or "Frame upload failed.")
                )
            self._status(
                f"Camera photos: sent {width}×{height} · {len(jpeg)//1024} KB"
            )
        except Exception as exc:
            message = str(exc)[:300]
            self._finish_error(request_id, message)
            self._status(f"Camera photos: {message[:100]}")

    def _worker(self) -> None:
        if not self.configured:
            self._status("Camera photos: cloud configuration incomplete")
            return
        try:
            health = self._request("GET", "/api/camera/health", timeout=(8, 18))
            if int(health.get("camera_api_version", 0)) < 3:
                raise SnapshotCameraError("Deploy the photo-mode dashboard update.")
        except requests.RequestException as exc:
            self._status(f"Camera photos: offline · {concise_network_error(exc)}")
        except Exception as exc:
            self._status(f"Camera photos: {str(exc)[:100]}")

        while not self._stopped.wait(self.poll_seconds):
            try:
                data = self._request("GET", "/api/camera/pending")
                request_id = str(data.get("request_id") or "").strip()
                proof = str(data.get("proof") or "").strip()
                if request_id:
                    self._handle(request_id, proof)
                else:
                    self._status("Camera photos: ready · waiting for request")
            except requests.RequestException as exc:
                self._status(
                    f"Camera photos: offline · {concise_network_error(exc)}"
                )
                self._stopped.wait(10)
            except Exception as exc:
                self._status(f"Camera photos: {str(exc)[:100]}")
                self._stopped.wait(10)
