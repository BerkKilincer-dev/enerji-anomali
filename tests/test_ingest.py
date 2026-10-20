import json

import pandas as pd
import pytest

from src.ingest import replay
from src.ingest.mqtt_ingest import Reading, _hourly, parse_message, to_local_hour


def test_parse_message():
    reading = parse_message(
        "meters/acme-hq", json.dumps({"ts": "2026-10-19T14:00:00+03:00", "kwh": 12.5}).encode()
    )
    assert reading.external_id == "acme-hq" and reading.kwh == 12.5
    assert parse_message("meters/x", b'{"ts": "2026-10-19T14:00", "kwh": -1}').kwh is None
    assert parse_message("meters/x", b'{"ts": "2026-10-19T14:00", "kwh": 1e400}').kwh is None
    with pytest.raises(KeyError):
        parse_message("meters/x", b'{"kwh": 1}')


def test_timestamps_land_on_local_hours():
    assert to_local_hour(pd.Timestamp("2026-10-19T11:20:00Z"), "Europe/Istanbul") == pd.Timestamp(
        "2026-10-19 14:00"
    )
    assert to_local_hour(pd.Timestamp("2026-10-19 14:20"), "Europe/Istanbul") == pd.Timestamp(
        "2026-10-19 14:00"
    )


def test_hourly_batch_keeps_last_and_drops_future():
    now = pd.Timestamp.now(tz="Europe/Istanbul").tz_localize(None).floor("h")
    group = [
        Reading("x", now - pd.Timedelta(hours=1), 1.0),
        Reading("x", now - pd.Timedelta(hours=1), 2.0),
        Reading("x", now, None),
        Reading("x", now + pd.Timedelta(days=2), 3.0),
    ]
    out = _hourly(group, "Europe/Istanbul")
    assert out["kwh"].tolist()[0] == 2.0 and out["kwh"].isna().tolist() == [False, True]
    assert out["quality"].tolist() == ["ok", "missing"]


def test_replay_shift_keeps_weekdays_and_skips_leading_gaps():
    ts = pd.date_range("2016-03-07", periods=24 * 60, freq="h")
    readings = pd.DataFrame({"building_id": 1, "ts": ts, "kwh": 1.0})
    readings.loc[:23, "kwh"] = None
    now = pd.Timestamp("2026-10-19 15:00")
    series = replay.shifted_series(readings, 1, days=28, now=now)
    assert series.index[0] >= now - pd.Timedelta(days=28)
    assert series.index[0] - pd.Timedelta(weeks=1) < now - pd.Timedelta(days=28)
    assert series.index[0].dayofweek == ts[24].dayofweek and series.notna().iloc[0]
    with pytest.raises(ValueError):
        replay.shifted_series(readings, 2, days=28, now=now)


class FakeClient:
    def __init__(self):
        self.sent = []

    def publish(self, topic, payload, qos):
        self.sent.append((topic, json.loads(payload)))


def test_replay_sends_all_buildings_of_an_hour_together(monkeypatch):
    ts = pd.date_range("2026-10-01", periods=3, freq="h")
    rows = replay.stack(
        {
            "demo-1": pd.Series([1.0, None, 3.0], index=ts),
            "demo-2": pd.Series([5.0, 6.0], index=ts[1:]),
        }
    )
    sleeps, client = [], FakeClient()
    monkeypatch.setattr(replay, "local_now", lambda: pd.Timestamp("2026-10-02"))
    monkeypatch.setattr(replay.time, "sleep", sleeps.append)
    replay.publish(client, rows, speed=0.1)
    assert sleeps == [0.1, 0.1, 0.1]
    assert [topic for topic, _ in client.sent] == [
        "meters/demo-1",
        "meters/demo-1",
        "meters/demo-2",
        "meters/demo-1",
        "meters/demo-2",
    ]
    assert client.sent[1][1] == {"ts": "2026-10-01T01:00:00", "kwh": None}


def test_replay_waits_for_hours_that_have_not_ended(monkeypatch):
    ts = pd.date_range("2026-10-01 10:00", periods=2, freq="h")
    rows = replay.stack({"demo-1": pd.Series([1.0, 2.0], index=ts)})
    sleeps = []
    monkeypatch.setattr(replay, "local_now", lambda: pd.Timestamp("2026-10-01 11:30"))
    monkeypatch.setattr(replay.time, "sleep", sleeps.append)
    replay.publish(FakeClient(), rows, speed=0.1)
    assert sleeps == [0.1, 1800.0]


def test_injection_magnitude_scales_night_hours_only():
    ts = pd.date_range("2026-10-01", periods=48, freq="h")
    out = replay.inject(pd.Series(10.0, index=ts), "night_base_load", 1, ts[0], magnitude=2.0)
    night = out.index.hour < 6
    assert (out[night] == 20.0).all() and (out[~night] == 10.0).all()
    assert (
        replay.inject(pd.Series(10.0, index=ts), "night_base_load", 1, ts[0])[night] == 30.0
    ).all()


def test_injections_still_run_at_the_current_hour():
    now = pd.Timestamp("2026-10-19 14:00")
    assert replay.inject_start("spike", now) == now - pd.Timedelta(hours=2)
    assert replay.inject_start("night_base_load", now) == now - replay.INJECT_AGE
