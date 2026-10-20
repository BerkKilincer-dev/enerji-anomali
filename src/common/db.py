"""Postgres connection helpers shared by all services."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg


def normalize_url(url: str) -> str:
    # SQLAlchemy-style URLs are accepted for convenience.
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@contextmanager
def connect(url: str, autocommit: bool = False) -> Iterator[psycopg.Connection]:
    with psycopg.connect(normalize_url(url), autocommit=autocommit, connect_timeout=15) as conn:
        yield conn
