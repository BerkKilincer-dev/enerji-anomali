"""Write model output (hourly predictions and anomaly events) to the shared database."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import psycopg

from src.common.db import connect
from src.data.store import copy_frame
from src.models import registry
from src.models.events import EVENT_COLUMNS

log = logging.getLogger(__name__)

PREDICTION_COLUMNS = [
    "building_id",
    "ts",
    "origin",
    "kwh",
    "expected_kwh",
    "detector_score",
    "residual_score",
    "rule_score",
    "rule_type",
    "source",
    "alarm",
    "model_version",
]
ANOMALY_COLUMNS = [*EVENT_COLUMNS, "origin", "model_version"]


def prediction_rows(scored: pd.DataFrame, origin: str, version: str) -> pd.DataFrame:
    return scored.assign(origin=origin, model_version=version)[PREDICTION_COLUMNS]


def anomaly_rows(events: pd.DataFrame, origin: str, version: str) -> pd.DataFrame:
    return events.assign(origin=origin, model_version=version)[ANOMALY_COLUMNS]


def replace_batch(
    conn: psycopg.Connection, scored: pd.DataFrame, events: pd.DataFrame, version: str
) -> None:
    """Swap the offline evaluation output for a new model version in one transaction."""
    conn.execute("DELETE FROM predictions WHERE origin = 'batch'")
    conn.execute("DELETE FROM anomalies WHERE origin = 'batch'")
    copy_frame(conn, "predictions", prediction_rows(scored, "batch", version))
    copy_frame(conn, "anomalies", anomaly_rows(events, "batch", version))


def publish_results(
    database_url: str, model_dir: Path, scored: pd.DataFrame, events: pd.DataFrame
) -> str:
    version = registry.publish(model_dir, database_url)
    with connect(database_url) as conn:
        replace_batch(conn, scored, events, version)
    log.info("published %s: %d predictions, %d anomalies", version, len(scored), len(events))
    return version
