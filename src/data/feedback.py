"""Operator verdicts on alarms, turned into training labels."""

from __future__ import annotations

import numpy as np
import pandas as pd
import psycopg

VERDICT_LABEL = {"confirmed": 1, "false_alarm": 0, "planned": 0}
COLUMNS = ["building_id", "ts_start", "ts_end", "verdict", "created_at"]


def fetch_feedback(conn: psycopg.Connection) -> pd.DataFrame:
    rows = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM anomaly_feedback").fetchall()
    return pd.DataFrame(rows, columns=COLUMNS)


def apply_feedback(readings: pd.DataFrame, feedback: pd.DataFrame) -> pd.DataFrame:
    """Each verdict labels every reading in its hour range; the latest verdict wins."""
    if feedback.empty:
        return readings
    label = readings["anomaly"].astype("float32").to_numpy(copy=True)
    building, ts = readings["building_id"].to_numpy(), readings["ts"].to_numpy()
    ordered = feedback.sort_values("created_at")
    for b, rows in ordered.groupby("building_id", sort=False):
        idx = np.flatnonzero(building == b)
        for row in rows.itertuples():
            hit = (ts[idx] >= np.datetime64(row.ts_start)) & (ts[idx] <= np.datetime64(row.ts_end))
            label[idx[hit]] = VERDICT_LABEL[row.verdict]
    return readings.assign(anomaly=pd.Series(label, index=readings.index).astype("Int8"))
