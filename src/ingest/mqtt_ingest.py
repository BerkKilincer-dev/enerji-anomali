"""MQTT consumer: hourly meter messages into the readings table.

Usage: python -m src.ingest.mqtt_ingest
Topic meters/<external_id>, payload {"ts": "2026-10-19T14:00:00+03:00", "kwh": 12.5}.
A message carries the energy used in the hour starting at ts; sub-hourly meters are
summed by the gateway. Naive timestamps are read in the building timezone.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.common.db import connect
from src.config import require_database_url
from src.data.customer import upsert_readings
from src.data.validate import FUTURE_TOLERANCE
from src.ingest.broker import start_client

log = logging.getLogger(__name__)

TOPIC = "meters/+"
FLUSH_SECONDS = 5
MAX_BUFFERED = 100_000  # readings kept while the database is unreachable


@dataclass(frozen=True)
class Reading:
    external_id: str
    ts: pd.Timestamp
    kwh: float | None


def parse_message(topic: str, payload: bytes) -> Reading:
    external_id = topic.split("/", 1)[1]
    body = json.loads(payload)
    ts = pd.Timestamp(body["ts"])
    kwh = body.get("kwh")
    if kwh is not None:
        kwh = float(kwh)
        if not (np.isfinite(kwh) and kwh >= 0):
            kwh = None
    return Reading(external_id, ts, kwh)


def to_local_hour(ts: pd.Timestamp, timezone: str) -> pd.Timestamp:
    local = ts.tz_convert(timezone).tz_localize(None) if ts.tzinfo else ts
    return local.floor("h")


class Buffer:
    """Collects readings per building and writes them in batches."""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.items: list[Reading] = []
        self.lock = threading.Lock()

    def add(self, reading: Reading) -> None:
        with self.lock:
            self.items.append(reading)

    def flush(self) -> int:
        with self.lock:
            items, self.items = self.items, []
        if not items:
            return 0
        try:
            return self._write(items)
        except Exception:
            with self.lock:
                self.items = items + self.items
                if len(self.items) > MAX_BUFFERED:
                    log.warning(
                        "buffer full, dropping %d oldest readings", len(self.items) - MAX_BUFFERED
                    )
                    self.items = self.items[-MAX_BUFFERED:]
            raise

    def _write(self, items: list[Reading]) -> int:
        written = 0
        groups = _group(items)
        with connect(self.database_url) as conn:
            # Looked up on every flush, so a changed timezone applies without a restart.
            rows = conn.execute(
                "SELECT external_id, building_id, timezone FROM buildings "
                "WHERE external_id = ANY(%s)",
                (list(groups),),
            ).fetchall()
            buildings = {external_id: rest for external_id, *rest in rows}
            for external_id, group in groups.items():
                if external_id not in buildings:
                    log.warning("dropping data for unregistered building %r", external_id)
                    continue
                building_id, timezone = buildings[external_id]
                written += upsert_readings(conn, building_id, _hourly(group, timezone), "mqtt")
        return written


def _group(items: list[Reading]) -> dict[str, list[Reading]]:
    groups: dict[str, list[Reading]] = {}
    for item in items:
        groups.setdefault(item.external_id, []).append(item)
    return groups


def _hourly(group: list[Reading], timezone: str) -> pd.DataFrame:
    now = pd.Timestamp.now(tz=timezone).tz_localize(None)
    frame = pd.DataFrame(
        {"ts": [to_local_hour(r.ts, timezone) for r in group], "kwh": [r.kwh for r in group]}
    )
    frame = frame[frame["ts"] <= now + FUTURE_TOLERANCE].drop_duplicates("ts", keep="last")
    quality = np.where(frame["kwh"].isna(), "missing", "ok")
    return frame.assign(meter_raw=frame["kwh"], quality=quality)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    buffer = Buffer(require_database_url())

    def on_message(client, userdata, msg) -> None:
        try:
            buffer.add(parse_message(msg.topic, msg.payload))
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            log.warning("bad message on %s: %s", msg.topic, exc)

    client = start_client(lambda c, *_: c.subscribe(TOPIC, qos=1), on_message)
    log.info("listening on %s", TOPIC)
    try:
        while True:
            time.sleep(FLUSH_SECONDS)
            try:
                if written := buffer.flush():
                    log.info("wrote %d readings", written)
            except Exception:
                log.exception("flush failed")
    finally:
        client.loop_stop()


if __name__ == "__main__":
    main()
