import numpy as np
import pandas as pd
import pytest

from src.data.catalog import CATALOG_PATH, read_catalog
from src.ingest import live_demo as ld
from tests.conftest import make_tables

NOW = pd.Timestamp("2026-11-02 01:12")


def test_committed_live_meters_are_valid_and_not_in_the_catalog():
    meters = ld.read_meters()
    assert len(meters) == 5 and set(meters["scenario"]) <= set(ld.INJECT_AT)
    assert not set(meters["external_id"]) & set(read_catalog(CATALOG_PATH)["external_id"])


def test_read_meters_rejects_unknown_values(tmp_path):
    path = tmp_path / "live.csv"
    path.write_text(
        "external_id,location,asset_name,equipment_type,scenario\nx,A,B,klima,drift\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown scenario: drift"):
        ld.read_meters(path)


@pytest.mark.parametrize(
    ("now", "start"),
    [
        ("2026-11-02 01:12", "2026-10-31 22:00"),
        ("2026-11-02 23:30", "2026-11-01 22:00"),
        ("2026-11-02 22:00", "2026-11-01 22:00"),
    ],
)
def test_fast_play_starts_at_22_one_to_two_days_back(now, start):
    assert ld.fast_start(pd.Timestamp(now)) == pd.Timestamp(start)


def test_backlog_at_once_then_one_hour_per_interval_never_past_now():
    start = ld.fast_start(NOW)
    assert ld.due_until(start, 5, 60, NOW) == start
    assert ld.due_until(start, 125, 60, NOW) == start + 2 * ld.HOUR
    # After catching up, an hour goes out only once it has ended.
    assert ld.due_until(start, 10**6, 60, NOW) == pd.Timestamp("2026-11-02 00:00")


def test_plan_puts_each_problem_next_to_fast_play():
    tables = make_tables(building_ids=(1, 2), days=60, start=pd.Timestamp("2016-01-01"))
    meters = pd.DataFrame(
        {
            "external_id": ["a", "b"],
            "location": ["Hol", "Depo"],
            "asset_name": ["Işık", "Soğuk oda"],
            "equipment_type": ["aydinlatma", "sogutma"],
            "scenario": ["night_base_load", "stuck"],
        }
    )
    rows, start = ld.plan(tables.readings, meters, {"a": 1, "b": 2}, NOW)
    assert set(rows["external_id"]) == {"canli-1", "canli-2"}
    assert rows["ts"].max() <= NOW + ld.AHEAD
    assert rows["ts"].min() <= start - pd.Timedelta(days=ld.BACKLOG_DAYS)
    hall = rows[rows["external_id"] == "canli-1"].set_index("ts")["kwh"]
    night = start + pd.Timedelta(hours=3)  # 01:00 after the 22:00 start
    week_before = night - pd.Timedelta(weeks=1)
    assert hall[night] > 2 * hall[week_before]
    depot = rows[rows["external_id"] == "canli-2"].set_index("ts")["kwh"]
    stuck = depot[start + ld.INJECT_AT["stuck"] : start - ld.HOUR]
    assert len(stuck) == 24 and np.allclose(stuck, stuck.iloc[0])
