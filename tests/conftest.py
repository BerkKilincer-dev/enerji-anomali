from __future__ import annotations

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

from src import config
from src.config import get_settings
from src.data.dataset import model_frame
from src.data.prepare import Tables
from src.features.build_features import DETECTOR_FEATURES, EXPECTED_FEATURES, build_features
from src.models.detector import AnomalyScorer, Thresholds

START = pd.Timestamp("2026-01-05")


def daily_profile(ts: pd.DatetimeIndex, base: float) -> np.ndarray:
    workday = ts.dayofweek < 5
    return np.where(workday & ts.hour.isin(range(8, 19)), base * 2.0, base)


def make_tables(building_ids=(100000, 100001), days=35, start=START, seed=0) -> Tables:
    rng = np.random.default_rng(seed)
    ts = pd.date_range(start, periods=days * 24, freq="h")
    buildings = pd.DataFrame(
        {
            "building_id": np.array(building_ids, dtype="int32"),
            "external_id": [f"test-{b}" for b in building_ids],
            "source": "customer",
            "site_id": pd.array([pd.NA] * len(building_ids), dtype="Int16"),
            "weather_key": "geo:41.01,28.98",
            "primary_use": "Office",
            "sub_use": None,
            "square_feet": 50000.0,
            "year_built": pd.array([2000] * len(building_ids), dtype="Int16"),
            "timezone": "Europe/Istanbul",
            "country": "TR",
            "lat": 41.0082,
            "lng": 28.9784,
        }
    )
    readings = pd.concat(
        [
            pd.DataFrame(
                {
                    "building_id": np.int32(b),
                    "ts": ts,
                    "kwh": (daily_profile(ts, 50.0 + 10 * i) * rng.normal(1, 0.03, len(ts))).astype(
                        "float32"
                    ),
                    "meter_raw": np.float32("nan"),
                    "anomaly": pd.array([0] * len(ts), dtype="Int8"),
                    "source": "customer",
                }
            )
            for i, b in enumerate(building_ids)
        ],
        ignore_index=True,
    )
    weather = pd.DataFrame(
        {
            "weather_key": "geo:41.01,28.98",
            "ts": ts,
            "air_temp": (10 + 5 * np.sin(np.arange(len(ts)) / 24 * 2 * np.pi)).astype("float32"),
            "dew_temp": np.float32(5),
            "wind_speed": np.float32(3),
            "cloud_cover": np.float32(4),
            "precip_1h": np.float32(0),
            "sea_level_pressure": np.float32(1013),
        }
    )
    return Tables(buildings, weather, readings)


@pytest.fixture
def fresh_settings(monkeypatch):
    """Settings from the test's environment only: no cache and no developer .env."""
    monkeypatch.setattr(config, "_load_dotenv", lambda: None)
    get_settings.cache_clear()
    yield get_settings
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def tables() -> Tables:
    return make_tables()


@pytest.fixture(scope="session")
def scorer(tables) -> AnomalyScorer:
    """Small LightGBM pair trained on synthetic buildings; good enough to exercise the paths."""
    frame = model_frame(tables)
    features = build_features(frame)
    present = frame["kwh"].notna().to_numpy()
    y_expected = np.log1p(frame["kwh"].to_numpy())
    rng = np.random.default_rng(1)
    y_detector = (rng.random(len(frame)) < 0.05).astype(int)
    params = {"verbose": -1, "min_data_in_leaf": 20, "num_leaves": 7, "seed": 1}
    expected = lgb.train(
        {**params, "objective": "l1"},
        lgb.Dataset(features.loc[present, EXPECTED_FEATURES], y_expected[present]),
        30,
    )
    detector = lgb.train(
        {**params, "objective": "binary"},
        lgb.Dataset(features.loc[present, DETECTOR_FEATURES], y_detector[present]),
        30,
    )
    return AnomalyScorer(expected, detector, Thresholds(detector=0.99, residual=3.0), "test")
