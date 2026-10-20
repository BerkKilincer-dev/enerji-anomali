"""Write model output (hourly predictions and anomaly events) to the database.

Usage: python -m src.models.publish [--if-missing]
Loads the committed model in MODEL_DIR (models_out/), scores the test meters and stores the
model, predictions and anomaly events, so nobody has to train before using the panel.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd
import psycopg

from src.common.db import connect
from src.config import get_settings, require_database_url
from src.data.dataset import ensure_tables, model_frame
from src.data.store import copy_frame
from src.models import registry
from src.models.events import EVENT_COLUMNS, build_events

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


def is_published(database_url: str, version: str) -> bool:
    """True when this model version and its batch output are already in the database."""
    with connect(database_url) as conn:
        return conn.execute(
            "SELECT EXISTS (SELECT 1 FROM predictions WHERE origin = 'batch' "
            "AND model_version = %s)",
            (version,),
        ).fetchone()[0]


def publish_saved(database_url: str, model_dir: Path, if_missing: bool = False) -> str:
    """Score the test meters with a saved model and publish, the same output as train --publish."""
    scorer = registry.load(model_dir)
    if if_missing and is_published(database_url, scorer.version):
        log.info("model %s already published, nothing to do", scorer.version)
        return scorer.version
    frame = model_frame(ensure_tables())
    scored = scorer.score(frame[frame["split"] == "test"].reset_index(drop=True))
    events = build_events(scored, get_settings().tariff_tl_per_kwh)
    return publish_results(database_url, model_dir, scored, events)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--if-missing", action="store_true", help="skip when this version is already published"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    publish_saved(require_database_url(), get_settings().model_dir, args.if_missing)


if __name__ == "__main__":
    main()
