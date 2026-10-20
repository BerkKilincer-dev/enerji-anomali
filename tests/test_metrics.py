import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import average_precision_score

from src.models import metrics


def test_hourly_metrics_count_hours_and_events():
    y = np.array([0, 1, 1, 0, 0, 0, 1, 0])
    alarm = np.array([0, 1, 0, 0, 1, 0, 0, 0], dtype=bool)
    out = metrics.at_threshold(y, alarm, np.ones(8, dtype=int))
    assert out["precision"] == 0.5
    assert out["recall"] == pytest.approx(1 / 3)
    assert out["event_recall"] == 0.5
    assert out["event_precision"] == 0.5


def test_day_metrics_count_a_day_once_if_any_hour_is_anomalous():
    ts = pd.date_range("2016-01-01", periods=72, freq="h")
    building = np.r_[np.ones(48, dtype=int), np.full(24, 2)]
    day = ts.normalize().to_numpy()
    y = np.zeros(72)
    y[[5, 6, 30, 60]] = 1  # building 1 day 1, building 1 day 2, building 2 day 3
    alarm = np.zeros(72, dtype=bool)
    alarm[[6, 40]] = True  # both building 1 days, none on building 2
    score = alarm.astype(float)
    out = metrics.daily(y, score, alarm, building, day)
    assert out["day_precision"] == 1.0
    assert out["day_recall"] == pytest.approx(2 / 3)
    assert out["day_f1"] == pytest.approx(0.8)
    assert 0 < out["day_pr_auc"] <= 1


def test_alarms_separated_by_dropped_hours_are_separate_events():
    y, alarm = np.array([1, 0]), np.array([True, True])
    hour = np.array([0, 30])  # adjacent rows once the unlabeled hours between them are dropped
    merged = metrics.at_threshold(y, alarm, np.ones(2, dtype=int))
    split = metrics.at_threshold(y, alarm, np.ones(2, dtype=int), hour)
    assert merged["event_precision"] == 1.0
    assert split["event_precision"] == 0.5


def test_bootstrap_matches_resampling_whole_buildings():
    rng = np.random.default_rng(0)
    building = np.repeat(np.arange(6), 40)
    y = (rng.random(240) < 0.15).astype(int)
    score = np.round(rng.random(240) + 0.5 * y, 1)  # rounded, so scores tie
    expected = []
    draws = np.random.default_rng(7)
    for _ in range(30):
        rows = np.concatenate([np.flatnonzero(building == b) for b in draws.choice(6, 6)])
        if y[rows].any():
            expected.append(average_precision_score(y[rows], score[rows]))
    low, high = metrics.bootstrap_pr_auc(y, score, building, n=30, seed=7)
    assert low == pytest.approx(np.quantile(expected, 0.025))
    assert high == pytest.approx(np.quantile(expected, 0.975))
