"""Live scoring: score new hours of every active building and store alarms.

Usage: python -m src.scoring.service [--once] [--interval 300] [--include-lead] [--no-weather]
Pulls the latest model from model_artifacts (or MODEL_DIR), reads each building's new
hours with enough history to score them as a full rescore would, and writes predictions
and anomaly events. New events go to Telegram when TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
are set.
"""

from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd
import psycopg

from src.common.db import connect
from src.config import get_settings, require_database_url
from src.data import weather_api
from src.data.customer import try_backfill_weather
from src.data.dataset import model_frame
from src.data.store import fetch_tables, upsert_frame
from src.models import registry
from src.models.detector import CONTEXT_HOURS, REVISION_HOURS, AnomalyScorer
from src.models.events import MAX_GAP_HOURS, build_events
from src.models.publish import anomaly_rows, prediction_rows
from src.scoring.alerts import send_alerts, telegram_from_settings

log = logging.getLogger(__name__)

REVISION = timedelta(hours=REVISION_HOURS)
CONTEXT = timedelta(hours=CONTEXT_HOURS) + REVISION
OPEN_EVENT_GAP = REVISION + timedelta(hours=MAX_GAP_HOURS)


@dataclass(frozen=True)
class Pending:
    building_id: int
    last_scored: datetime | None
    tariff: float | None


def pending_buildings(conn: psycopg.Connection, include_lead: bool) -> list[Pending]:
    rows = conn.execute(
        """
        SELECT b.building_id, s.last_scored_ts, b.tariff_tl_per_kwh
        FROM buildings b
        LEFT JOIN scoring_state s USING (building_id)
        WHERE b.active AND (%s OR b.source <> 'lead') AND EXISTS (
            SELECT 1 FROM readings r WHERE r.building_id = b.building_id
            AND r.ts > COALESCE(s.last_scored_ts, '-infinity'::timestamp))
        ORDER BY b.building_id
        """,
        (include_lead,),
    ).fetchall()
    return [Pending(*row) for row in rows]


def load_scorer(database_url: str) -> AnomalyScorer:
    try:
        return registry.fetch_latest(database_url)
    except LookupError:
        log.info("no model in the database, loading %s", get_settings().model_dir)
        return registry.load(get_settings().model_dir)


def refresh_scorer(database_url: str, scorer: AnomalyScorer) -> AnomalyScorer:
    """Switch to a newly published bundle; keep the current one on any error."""
    try:
        version = registry.latest_version(database_url)
        if version is None or version == scorer.version:
            return scorer
        scorer = registry.fetch_latest(database_url)
        log.info("switched to model %s", scorer.version)
    except Exception:
        log.exception("model check failed, keeping %s", scorer.version)
    return scorer


def write_predictions(conn: psycopg.Connection, scored: pd.DataFrame, version: str) -> int:
    rows = prediction_rows(scored, "live", version)
    return upsert_frame(conn, "predictions", rows, ("building_id", "ts", "origin"))


def write_events(conn: psycopg.Connection, events: pd.DataFrame, version: str) -> int:
    """Insert new events and extend the ones that continue from the previous cycle."""
    rows = anomaly_rows(events, "live", version)
    return upsert_frame(conn, "anomalies", rows, ("building_id", "ts_start", "origin"))


def history_start(conn: psycopg.Connection, item: Pending) -> datetime | None:
    """First hour to read; an event that may continue is rescored from its own start."""
    last = item.last_scored
    if last is None:
        return None
    open_start = conn.execute(
        "SELECT min(ts_start) FROM anomalies WHERE building_id = %s AND origin = 'live' "
        "AND ts_end >= %s",
        (item.building_id, last - OPEN_EVENT_GAP),
    ).fetchone()[0]
    return min(last, open_start or last) - CONTEXT


def score_building(
    conn: psycopg.Connection, scorer: AnomalyScorer, item: Pending, default_tariff: float
) -> int:
    """Score hours after last_scored for one building in one transaction; returns new hours."""
    last = item.last_scored
    tables = fetch_tables(conn, [item.building_id], history_start(conn, item))
    scored = scorer.score(model_frame(tables))
    new_hours = int((scored["ts"] > last).sum()) if last else len(scored)
    if not new_hours:
        return 0
    revised = scored if last is None else scored[scored["ts"] > last - REVISION]
    write_predictions(conn, revised, scorer.version)
    # Events are built over the whole history read, so one that spans cycles keeps its start.
    events = build_events(scored, item.tariff if item.tariff is not None else default_tariff)
    if last is not None:
        events = events[events["ts_end"] > last]
    written = write_events(conn, events, scorer.version)
    conn.execute(
        "INSERT INTO scoring_state (building_id, last_scored_ts) VALUES (%s, %s) "
        "ON CONFLICT (building_id) DO UPDATE SET last_scored_ts = EXCLUDED.last_scored_ts, "
        "updated_at = now()",
        (item.building_id, scored["ts"].max().to_pydatetime()),
    )
    log.info(
        "building %d: %d hours scored, %d events written", item.building_id, new_hours, written
    )
    return new_hours


def run_once(
    database_url: str,
    scorer: AnomalyScorer,
    include_lead: bool = False,
    weather: Callable | None = None,
    notify: Callable[[str], None] | None = None,
) -> int:
    """Score pending buildings, one transaction each; `weather` and `notify` are optional."""
    default_tariff = get_settings().tariff_tl_per_kwh
    total = 0
    with connect(database_url, autocommit=True) as conn:
        for item in pending_buildings(conn, include_lead):
            try:
                if weather is not None:
                    since = item.last_scored.date() if item.last_scored else None
                    try_backfill_weather(conn, item.building_id, weather, since)
                with conn.transaction():
                    total += score_building(conn, scorer, item, default_tariff)
            except Exception:
                log.exception("building %d failed, will retry next cycle", item.building_id)
        if notify is not None:
            try:
                send_alerts(conn, notify)
            except Exception:
                log.exception("alerts failed, will retry next cycle")
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="score once and exit")
    parser.add_argument("--interval", type=int, default=300, help="seconds between cycles")
    parser.add_argument("--include-lead", action="store_true", help="also score LEAD buildings")
    parser.add_argument("--no-weather", action="store_true", help="skip Open-Meteo backfill")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    url = require_database_url()
    scorer = load_scorer(url)
    telegram = telegram_from_settings()
    log.info(
        "scoring with model %s, telegram alerts %s", scorer.version, "on" if telegram else "off"
    )
    weather = None if args.no_weather else weather_api.fetch
    while True:
        scorer = refresh_scorer(url, scorer)
        run_once(url, scorer, args.include_lead, weather, telegram.send if telegram else None)
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
