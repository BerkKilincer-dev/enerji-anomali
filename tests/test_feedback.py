import pandas as pd

from src.data.feedback import apply_feedback


def test_latest_verdict_labels_its_hour_range():
    readings = pd.DataFrame(
        {
            "building_id": [1, 1, 1, 2],
            "ts": pd.to_datetime(
                ["2026-10-01 00:00", "2026-10-01 01:00", "2026-10-01 02:00", "2026-10-01 01:00"]
            ),
            "anomaly": pd.array([None, None, 0, None], dtype="Int8"),
        }
    )
    feedback = pd.DataFrame(
        {
            "building_id": [1, 1],
            "ts_start": pd.to_datetime(["2026-10-01 00:00", "2026-10-01 01:00"]),
            "ts_end": pd.to_datetime(["2026-10-01 01:00", "2026-10-01 02:00"]),
            "verdict": ["confirmed", "false_alarm"],
            "created_at": pd.to_datetime(["2026-10-02", "2026-10-03"], utc=True),
        }
    )
    out = apply_feedback(readings, feedback)
    assert out["anomaly"].tolist()[:3] == [1, 0, 0]
    assert out["anomaly"].isna().tolist()[3]


def test_no_feedback_leaves_readings_untouched():
    readings = pd.DataFrame(
        {
            "building_id": [1],
            "ts": [pd.Timestamp("2026-10-01")],
            "anomaly": pd.array([None], dtype="Int8"),
        }
    )
    assert apply_feedback(readings, pd.DataFrame(columns=["building_id"])) is readings


def test_applied_labels_waste_and_sent_labels_nothing():
    readings = pd.DataFrame(
        {
            "building_id": [1, 1],
            "ts": pd.to_datetime(["2026-10-01 00:00", "2026-10-01 05:00"]),
            "anomaly": pd.array([0, 0], dtype="Int8"),
        }
    )
    hours = pd.to_datetime(["2026-10-01 00:00", "2026-10-01 00:00", "2026-10-01 05:00"])
    feedback = pd.DataFrame(
        {
            "building_id": [1, 1, 1],
            "ts_start": hours,
            "ts_end": hours,
            "verdict": ["applied", "sent", "sent"],
            "created_at": pd.to_datetime(["2026-10-02", "2026-10-03", "2026-10-03"], utc=True),
        }
    )
    assert apply_feedback(readings, feedback)["anomaly"].tolist() == [1, 0]
