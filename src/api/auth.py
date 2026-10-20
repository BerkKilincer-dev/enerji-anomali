"""Passwords, session tokens and the signed-in user.

Passwords are hashed with scrypt from the standard library. A session is a random token in an
HttpOnly cookie; the database keeps only its SHA-256, so a leaked table cannot be replayed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Literal

Role = Literal["admin", "operator", "viewer"]
ROLES: tuple[Role, ...] = ("admin", "operator", "viewer")
MIN_PASSWORD_LENGTH = 12
SESSION_SECONDS = 7 * 24 * 3600
COOKIE = "energy_session"

_N, _R, _P, _DKLEN = 2**14, 8, 1, 32


@dataclass(frozen=True)
class User:
    user_id: int
    tenant_id: int | None
    email: str
    name: str | None
    role: Role

    @property
    def is_platform(self) -> bool:
        """Platform staff have no tenant and see every building."""
        return self.tenant_id is None

    @property
    def can_write(self) -> bool:
        return self.role in ("admin", "operator")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        scheme, n, r, p, salt, digest = (stored or "").split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


# Unknown e-mails are checked against this hash too, so timing does not reveal which exist.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LoginThrottle:
    """At most `limit` failed logins per e-mail in `window` seconds, per API process."""

    def __init__(self, limit: int = 5, window: float = 900):
        self.limit = limit
        self.window = window
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _recent(self, email: str, now: float) -> deque[float]:
        failures = self._failures[email]
        while failures and failures[0] < now - self.window:
            failures.popleft()
        return failures

    def blocked(self, email: str) -> bool:
        with self._lock:
            return len(self._recent(email, time.monotonic())) >= self.limit

    def failed(self, email: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._recent(email, now).append(now)

    def succeeded(self, email: str) -> None:
        with self._lock:
            self._failures.pop(email, None)
