from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import requests

from network_http import ReliableHttpClient, concise_network_error

API_BASE = "https://pixe.la"


class PixelaError(RuntimeError):
    def __init__(self, message: str, http_status: int | None = None):
        super().__init__(message)
        self.http_status = http_status


@dataclass(frozen=True)
class PixelaResult:
    data: dict[str, Any]
    http_status: int
    duration_ms: int


class PixelaClient:
    def __init__(self, username: str, token: str, graph_id: str, timeout: int = 20):
        self.username = username.strip()
        self.token = token.strip()
        self.graph_id = graph_id.strip()
        self.timeout = timeout
        self.http = ReliableHttpClient(
            user_agent="Mojjss-Live-Activity-Pixela/2.1",
            retries=3,
            connect_timeout=10,
            read_timeout=max(15, int(timeout)),
        )

    @property
    def configured(self) -> bool:
        return bool(
            self.username
            and self.username != "change-me"
            and self.token
            and self.graph_id
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        token_required: bool = True,
        retries: int = 6,
        expect_json: bool = True,
        params: dict[str, Any] | None = None,
    ) -> PixelaResult | tuple[bytes, int, int]:
        headers = {"Content-Type": "application/json"}
        if token_required:
            if not self.token:
                raise PixelaError("Pixela token is missing.")
            headers["X-USER-TOKEN"] = self.token

        last_error: Exception | None = None
        for attempt in range(retries):
            started = time.perf_counter()
            try:
                response = self.http.request(
                    method,
                    API_BASE + path,
                    headers=headers,
                    json=body,
                    params=params,
                    timeout=(10, self.timeout),
                )
                duration_ms = int((time.perf_counter() - started) * 1000)
            except requests.RequestException as exc:
                last_error = exc
                if attempt + 1 < retries:
                    time.sleep(min(2**attempt, 8))
                    continue
                raise PixelaError(
                    f"Network unavailable: {concise_network_error(exc)}"
                ) from exc

            if not expect_json and response.ok:
                return response.content, response.status_code, duration_ms

            try:
                payload = response.json() if response.content else {}
            except ValueError:
                payload = {"message": response.text.strip() or response.reason}

            retryable = response.status_code == 503 or payload.get("isRejected") is True
            if retryable and attempt + 1 < retries:
                last_error = PixelaError(
                    payload.get("message", "Pixela temporarily rejected the request."),
                    response.status_code,
                )
                time.sleep(min(2**attempt, 8))
                continue

            if not response.ok or payload.get("isSuccess") is False:
                raise PixelaError(
                    payload.get("message", f"Pixela HTTP {response.status_code}"),
                    response.status_code,
                )

            return PixelaResult(payload, response.status_code, duration_ms)

        raise PixelaError(f"Pixela request failed after retries: {last_error}")

    def graph_definition(self) -> PixelaResult:
        return self._request(
            "GET",
            f"/v1/users/{self.username}/graphs/{self.graph_id}/graph-def",
        )  # type: ignore[return-value]

    def test_connection(self) -> PixelaResult:
        if not self.configured:
            raise PixelaError("Pixela username, token, or graph ID is missing.")
        return self.graph_definition()

    def put_daily_total(self, date: str, quantity: int) -> PixelaResult:
        return self._request(
            "PUT",
            f"/v1/users/{self.username}/graphs/{self.graph_id}/{date}",
            body={"quantity": str(int(quantity))},
        )  # type: ignore[return-value]

    def graph_svg(
        self,
        *,
        mode: str | None = "short",
        appearance: str | None = None,
        transparent: bool = False,
    ) -> tuple[bytes, int, int]:
        params: dict[str, Any] = {}
        if mode:
            params["mode"] = mode
        if appearance:
            params["appearance"] = appearance
        if transparent:
            params["transparent"] = "true"
        return self._request(
            "GET",
            f"/v1/users/{self.username}/graphs/{self.graph_id}",
            token_required=False,
            expect_json=False,
            params=params,
        )  # type: ignore[return-value]

    def update_graph_unit(self, unit: str) -> PixelaResult:
        return self._request(
            "PUT",
            f"/v1/users/{self.username}/graphs/{self.graph_id}",
            body={"unit": unit},
        )  # type: ignore[return-value]
