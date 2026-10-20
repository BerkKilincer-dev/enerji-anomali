from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from src.data.dataset import model_frame
from src.features.build_features import EXPECTED_FEATURES, build_features, regularize
from src.models.unsupervised import MIN_SCALE_HOURS, residual_score
from tests.conftest import make_tables


def test_regularize_fills_gaps_inside_each_building_only():
    df = pd.DataFrame(
        {
            "building_id": [1, 1, 2, 2],
            "ts": pd.to_datetime(
                ["2026-01-01 00:00", "2026-01-01 03:00", "2026-02-01 00:00", "2026-02-01 01:00"]
            ),
            "kwh": [1.0, 4.0, 5.0, 6.0],
            "primary_use": ["Office", None, "Retail", None],
        }
    )
    out = regularize(df)
    assert out.groupby("building_id").size().to_dict() == {1: 4, 2: 2}
    assert out.loc[out.building_id == 1, "kwh"].isna().sum() == 2
    assert set(out.loc[out.building_id == 2, "primary_use"]) == {"Retail"}


def test_build_features_rejects_gaps():
    df = pd.DataFrame(
        {
            "building_id": [1, 1],
            "ts": pd.to_datetime(["2026-01-01 00:00", "2026-01-01 02:00"]),
            "kwh": [1.0, 2.0],
        }
    )
    with pytest.raises(ValueError, match="regularize"):
        build_features(df)


def test_buildings_with_different_periods_share_one_frame():
    a = make_tables(building_ids=(100000,), days=21)
    b = make_tables(building_ids=(100001,), days=14, start=pd.Timestamp("2026-03-02"))
    tables = type(a)(
        *(
            pd.concat([getattr(a, n), getattr(b, n)], ignore_index=True).drop_duplicates()
            for n in a.FILES
        )
    )
    frame = model_frame(tables)
    features = build_features(frame)
    assert features.index.equals(frame.index)
    assert frame.groupby("building_id").size().to_dict() == {100000: 21 * 24, 100001: 14 * 24}
    shuffled = frame.sample(frac=1, random_state=0)
    pd.testing.assert_frame_equal(build_features(shuffled), features.loc[shuffled.index])


def test_residual_score_is_per_building_and_past_only():
    frame = pd.DataFrame(
        {
            "building_id": np.repeat([1, 2], [504, 336]),
            "ts": np.concatenate(
                [
                    pd.date_range("2026-01-01", periods=504, freq="h"),
                    pd.date_range("2026-02-01", periods=336, freq="h"),
                ]
            ),
        }
    )
    residual = np.random.default_rng(0).normal(0, 0.1, len(frame))
    score = residual_score(frame, residual)
    second = (frame.building_id == 2).to_numpy()
    np.testing.assert_allclose(score[second], residual_score(frame[second], residual[second]))
    shuffled = frame.sample(frac=1, random_state=0)
    np.testing.assert_allclose(
        residual_score(shuffled, residual[shuffled.index]), score[shuffled.index]
    )
    changed = residual.copy()
    changed[-1] = 50.0
    np.testing.assert_allclose(residual_score(frame, changed)[:-1], score[:-1])
    assert np.isnan(score[:MIN_SCALE_HOURS]).all()
    assert not np.isnan(score[MIN_SCALE_HOURS:504]).any()


def test_expected_features_ignore_the_current_reading(tables):
    frame = model_frame(tables)
    spiked = frame.copy()
    row = spiked.index[(spiked.building_id == 100000)][-5]
    spiked.loc[row, "kwh"] *= 10
    before, after = build_features(frame), build_features(spiked)
    cols = [c for c in EXPECTED_FEATURES if c != "primary_use"]
    np.testing.assert_allclose(
        before.loc[row, cols].astype(float), after.loc[row, cols].astype(float)
    )
    assert after.loc[row, "log_kwh"] > before.loc[row, "log_kwh"]


def test_partial_and_merged_hours_count_as_missing():
    tables = make_tables(building_ids=(100000,), days=1)
    quality = ["ok", "partial", "merged", None] * 6
    frame = model_frame(replace(tables, readings=tables.readings.assign(quality=quality)))
    assert frame["is_missing"].tolist() == [False, True, True, False] * 6
    assert "quality" not in frame


def test_features_use_only_the_current_hour_and_earlier(tables):
    frame = model_frame(tables)
    past = frame["ts"] <= frame["ts"].min() + pd.Timedelta(days=20, hours=7)
    future = frame.copy()
    future.loc[~past, "kwh"] *= 5  # any change after the cutoff must not move earlier features
    pd.testing.assert_frame_equal(build_features(frame[past]), build_features(future).loc[past])
