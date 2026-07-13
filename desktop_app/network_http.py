from __future__ import annotations

import time
from typing import Any

import requests

NETWORK_EXCEPTIONS = (
    requests.exceptions.ProxyError,
    requests.exceptions.SSLError,
    requests.exceptions.ConnectTimeout,
    requests.exceptions.ReadTimeout,
    requests.exceptions.ConnectionError,
)


def concise_network_error(exc: BaseException) -> str:
    text = str(exc).lower()
    if "proxy" in text or isinstance(exc, requests.exceptions.ProxyError):
        return "proxy connection failed"
    if "handshake" in text or isinstance(exc, requests.exceptions.SSLError):
        return "TLS handshake failed or timed out"
    if "timed out" in text or isinstance(exc, (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout)):
        return "network request timed out"
    if "refused" in text:
        return "connection was refused"
    if "name or service" in text or "getaddrinfo" in text:
        return "DNS lookup failed"
    return "network connection failed"


class ReliableHttpClient:
    """Try system proxy/VPN first, then a direct connection on transport failure."""

    def __init__(self, *, user_agent: str, retries: int = 3, connect_timeout: int = 10, read_timeout: int = 22) -> None:
        self.retries = max(1, int(retries))
        self.timeout = (max(2, int(connect_timeout)), max(4, int(read_timeout)))
        self.proxy_session = requests.Session()
        self.proxy_session.trust_env = True
        self.proxy_session.headers.update({"User-Agent": user_agent})
        self.direct_session = requests.Session()
        self.direct_session.trust_env = False
        self.direct_session.headers.update({"User-Agent": user_agent})

    def request(self, method: str, url: str, *, timeout=None, **kwargs: Any) -> requests.Response:
        last_error: BaseException | None = None
        request_timeout = timeout or self.timeout
        for attempt in range(self.retries):
            for session in (self.proxy_session, self.direct_session):
                try:
                    return session.request(method, url, timeout=request_timeout, **kwargs)
                except NETWORK_EXCEPTIONS as exc:
                    last_error = exc
            if attempt + 1 < self.retries:
                time.sleep(min(2 ** attempt, 5))
        raise requests.ConnectionError(concise_network_error(last_error or RuntimeError("unknown error"))) from last_error

    def close(self) -> None:
        self.proxy_session.close()
        self.direct_session.close()
