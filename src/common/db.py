"""Postgres connection helpers shared by all services."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.conninfo import conninfo_to_dict

SCHEMAS = ("core", "telemetry", "ml", "ops")
SEARCH_PATH = ",".join((*SCHEMAS, "public"))


def normalize_url(url: str) -> str:
    # SQLAlchemy-style URLs are accepted for convenience.
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def connection_options(url: str) -> str:
    # Options from the URL or PGOPTIONS stay; the search path never depends on database settings.
    given = conninfo_to_dict(url).get("options") or os.getenv("PGOPTIONS", "")
    return f"{given} -c search_path={SEARCH_PATH}".strip()


@contextmanager
def connect(url: str, autocommit: bool = False) -> Iterator[psycopg.Connection]:
    url = normalize_url(url)
    with psycopg.connect(
        url, autocommit=autocommit, connect_timeout=15, options=connection_options(url)
    ) as conn:
        yield conn
