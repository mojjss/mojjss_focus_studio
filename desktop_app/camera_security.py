from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Any


# Stable password-verifier cost used by the desktop and private camera server.
CAMERA_PBKDF2_ITERATIONS = 100_000
DEFAULT_ITERATIONS = CAMERA_PBKDF2_ITERATIONS


def derive_camera_password(
    password: str,
    *,
    salt_b64: str | None = None,
    iterations: int = DEFAULT_ITERATIONS,
) -> dict[str, Any]:
    password = password.strip()
    if len(password) < 10:
        raise ValueError(
            "Camera viewer password must contain at least 10 characters."
        )

    requested_iterations = int(iterations)
    if requested_iterations != CAMERA_PBKDF2_ITERATIONS:
        raise ValueError(
            "Camera viewer passwords must use exactly 100,000 PBKDF2 "
            "iterations for compatibility with this app."
        )

    salt = (
        base64.b64decode(salt_b64.encode("ascii"), validate=True)
        if salt_b64
        else secrets.token_bytes(16)
    )
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        CAMERA_PBKDF2_ITERATIONS,
        dklen=32,
    )
    return {
        "remote_camera_password_salt": base64.b64encode(salt).decode("ascii"),
        "remote_camera_password_hash": base64.b64encode(digest).decode("ascii"),
        "remote_camera_password_iterations": CAMERA_PBKDF2_ITERATIONS,
    }


def camera_password_iterations(config: dict[str, Any]) -> int:
    try:
        return int(config.get("remote_camera_password_iterations", 0))
    except (TypeError, ValueError):
        return 0


def camera_password_needs_migration(config: dict[str, Any]) -> bool:
    salt = str(config.get("remote_camera_password_salt", "")).strip()
    digest = str(config.get("remote_camera_password_hash", "")).strip()
    iterations = camera_password_iterations(config)
    return bool(
        salt
        and digest
        and iterations != CAMERA_PBKDF2_ITERATIONS
    )


def has_camera_password(config: dict[str, Any]) -> bool:
    salt = str(config.get("remote_camera_password_salt", "")).strip()
    digest = str(config.get("remote_camera_password_hash", "")).strip()
    return bool(
        salt
        and digest
        and camera_password_iterations(config)
        == CAMERA_PBKDF2_ITERATIONS
    )


def verify_camera_password(password: str, config: dict[str, Any]) -> bool:
    """Verify a viewer password without letting malformed input kill the HTTP request."""
    if not has_camera_password(config):
        return False

    # The configured password is always at least 10 characters. A shorter
    # candidate cannot match, and should be treated as a normal bad password
    # rather than raising ValueError from derive_camera_password().
    candidate = str(password or "").strip()
    if len(candidate) < 10:
        return False

    try:
        derived = derive_camera_password(
            candidate,
            salt_b64=str(config["remote_camera_password_salt"]),
            iterations=CAMERA_PBKDF2_ITERATIONS,
        )
    except (KeyError, TypeError, ValueError, base64.binascii.Error):
        return False

    return hmac.compare_digest(
        str(derived["remote_camera_password_hash"]),
        str(config["remote_camera_password_hash"]),
    )
