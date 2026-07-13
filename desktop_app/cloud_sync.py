from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

import requests

from network_http import ReliableHttpClient, concise_network_error


class CloudStateSynchronizer:
    """Single-worker, latest-state-wins two-way synchronization client."""

    def __init__(
        self,
        base_url: str,
        write_key: str,
        *,
        status_callback: Callable[[str], None] | None = None,
        result_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.write_key = write_key.strip()
        self.status_callback = status_callback
        self.result_callback = result_callback
        self.http = ReliableHttpClient(
            user_agent="Mojjss-Focus-Studio-Cloud-Sync/5.5",
            retries=2,
            connect_timeout=10,
            read_timeout=30,
        )
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=1)
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    @property
    def configured(self) -> bool:
        return bool(self.base_url.startswith("https://") and self.write_key)

    def _status(self, message: str) -> None:
        if self.status_callback:
            self.status_callback(message)

    def submit(self, payload: dict[str, Any]) -> None:
        if not self.configured or self._stopped.is_set():
            return
        try:
            self._queue.put_nowait(dict(payload))
        except queue.Full:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(dict(payload))
            except queue.Full:
                pass

    def _worker(self) -> None:
        while not self._stopped.is_set():
            try:
                payload = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if payload is None:
                break
            started = time.perf_counter()
            try:
                response = self.http.request(
                    "POST",
                    f"{self.base_url}/api/sync",
                    headers={
                        "Authorization": f"Bearer {self.write_key}",
                        "Content-Type": "application/json",
                    },
                    data=json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8"),
                )
                if not response.ok:
                    raise requests.HTTPError(
                        f"HTTP {response.status_code}: {response.text[:180]}",
                        response=response,
                    )
                result = response.json()
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                message = str(result.get("message") or "Synchronized")
                self._status(f"Cloud sync: {message} · {elapsed_ms} ms")
                if self.result_callback:
                    self.result_callback(result)
            except requests.HTTPError as exc:
                code = exc.response.status_code if exc.response is not None else "?"
                self._status(f"Cloud sync: server error · HTTP {code}")
            except requests.RequestException as exc:
                self._status(f"Cloud sync: offline · {concise_network_error(exc)}")
            except (ValueError, TypeError):
                self._status("Cloud sync: invalid server response")
            except Exception:
                self._status("Cloud sync: failed")

    def stop(self) -> None:
        if self._stopped.is_set():
            return
        self._stopped.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout=2)
        self.http.close()
