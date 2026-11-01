import numpy as np
import pandas as pd

from src.data.dataset import equipment_types, model_frame
from src.models import registry
from src.models.detector import CONTEXT_HOURS, ML_WARMUP_HOURS, RULE_WARMUP_HOURS, SOURCES
from src.models.events import _TEMPLATES, build_events
from src.models.rules import FLATLINE_HOURS, RULES, evaluate, persistent
from tests.conftest import make_tables


def test_new_buildings_wait_before_alarms(scorer):
    frame = model_frame(make_tables(building_ids=(100002,), days=20, seed=3))
    frame["kwh"] = frame["kwh"] * 5  # every hour looks wild
    scored = scorer.score(frame)
    hour = np.arange(len(scored))
    assert not scored.loc[hour < RULE_WARMUP_HOURS, "alarm"].any()
    early = scored.loc[hour < ML_WARMUP_HOURS, "source"]
    assert set(early.dropna()) <= {"rule"}


def test_injected_night_waste_becomes_one_costed_event(tables, scorer):
    frame = model_frame(tables)
    night = (
        (frame.building_id == 100000)
        & (frame.ts.dt.date == pd.Timestamp("2026-02-05").date())
        & frame.ts.dt.hour.isin(range(0, 6))
    )
    frame.loc[night, "kwh"] *= 3
    scored = scorer.score(frame)
    events = build_events(scored, tariff=4.0)
    hit = events[(events.building_id == 100000) & (events.rule_type == "night_base_load")]
    assert len(hit) == 1
    event = hit.iloc[0]
    assert event.ts_start == pd.Timestamp("2026-02-05 00:00")
    assert event.est_cost_tl == event.excess_kwh * 4.0 > 0
    assert event.explanation.startswith("2026-02-05 00:00-06:00: Gece tüketimi")


def test_registry_roundtrip_gives_identical_scores(tables, scorer, tmp_path):
    frame = model_frame(tables)
    loaded = registry.load(registry.save(scorer, tmp_path))
    a, b = scorer.score(frame), loaded.score(frame)
    assert loaded.version == scorer.version and loaded.thresholds == scorer.thresholds
    np.testing.assert_allclose(a["expected_kwh"], b["expected_kwh"])
    assert (a["alarm"] == b["alarm"]).all()


def test_single_off_hours_reading_is_not_waste():
    rule_type = pd.Series(["weekend_usage", None, "night_base_load", "night_base_load", "spike"])
    hits = np.array([True, False, True, True, True])
    building = np.zeros(5, dtype=int)
    assert persistent(hits, rule_type, building).tolist() == [False, False, True, True, True]


def test_new_hours_scored_with_context_match_a_full_rescore(scorer):
    frame = model_frame(make_tables(days=56))
    cut = frame["ts"].max() - pd.Timedelta(days=7)
    window = frame[frame["ts"] >= cut - pd.Timedelta(hours=CONTEXT_HOURS)]
    full, part = scorer.score(frame), scorer.score(window)
    pd.testing.assert_frame_equal(
        full[full["ts"] > cut].reset_index(drop=True),
        part[part["ts"] > cut].reset_index(drop=True),
        rtol=1e-4,
    )


def test_events_without_alarms_are_an_empty_sortable_frame(tables, scorer):
    scored = scorer.score(model_frame(tables)).assign(alarm=False)
    events = build_events(scored, tariff=4.0)
    assert events.empty and events.nlargest(5, "est_cost_tl").empty


def test_model_events_do_not_carry_a_rule_type():
    scored = pd.DataFrame(
        {
            "building_id": 1,
            "ts": pd.date_range("2026-02-05 01:00", periods=2, freq="h"),
            "alarm": True,
            "kwh": [90.0, 95.0],
            "expected_kwh": [50.0, 50.0],
            "detector_score": [0.9, 0.95],
            "rule_score": [0.6, 0.0],
            "source": "ml_sup",
            "rule_type": ["night_base_load", None],
            "rule_ratio": [1.8, None],
        }
    )
    event = build_events(scored, tariff=4.0).iloc[0]
    assert event.source == "ml_sup" and pd.isna(event.rule_type)
    assert event.explanation.endswith("(4,00 TL/kWh ile 340 TL).")


def test_every_rule_and_model_source_has_an_explanation():
    assert set(_TEMPLATES) == set(RULES) | set(SOURCES) - {"rule"}


def test_flatline_explanation_counts_the_equal_hours_before_the_rule_fired():
    scored = pd.DataFrame(
        {
            "building_id": 1,
            "ts": pd.date_range("2026-02-05 01:00", periods=4, freq="h"),
            "alarm": True,
            "kwh": [90.0, 40.0, 40.0, 40.0],
            "expected_kwh": 40.0,
            "detector_score": 0.0,
            "rule_score": [0.55, 0.6, 0.6, 0.6],
            "source": "rule",
            "rule_type": ["spike", "flatline", "flatline", "flatline"],
            "rule_ratio": [2.2, 1.0, 1.0, 1.0],
        }
    )
    event = build_events(scored, tariff=4.0).iloc[0]
    assert FLATLINE_HOURS == 6 and "toplam en az 8 saat" in event.explanation


def hours(start: str, n: int, **columns) -> pd.DataFrame:
    """Alarm hours shaped like AnomalyScorer.score output."""
    base = {
        "building_id": 1,
        "ts": pd.date_range(start, periods=n, freq="h"),
        "alarm": True,
        "kwh": 40.0,
        "expected_kwh": 40.0,
        "detector_score": 0.0,
        "rule_score": 0.6,
        "source": "rule",
        "rule_type": "flatline",
        "rule_ratio": 1.0,
    }
    return pd.DataFrame({**base, **columns})


def on_grid(*parts: pd.DataFrame) -> pd.DataFrame:
    """Alarm hours on a complete hourly grid with quiet hours between, as the scorer returns."""
    alarms = pd.concat(parts, ignore_index=True)
    span = pd.date_range(alarms["ts"].min(), alarms["ts"].max(), freq="h")
    quiet = hours(
        str(span[0]),
        len(span),
        alarm=False,
        rule_score=0.0,
        source=None,
        rule_type=None,
        rule_ratio=None,
    )
    quiet = quiet[~quiet["ts"].isin(alarms["ts"])]
    return pd.concat([quiet, alarms]).sort_values("ts", ignore_index=True)


def test_stuck_meter_days_are_one_data_issue_without_cost():
    # Stuck for three days; each night a few readings move and break the run.
    scored = on_grid(
        hours("2026-02-05 05:00", 19, kwh=90.0),
        hours("2026-02-06 05:00", 19),
        hours("2026-02-07 05:00", 19),
    )
    events = build_events(scored, tariff=4.0)
    assert len(events) == 1
    event = events.iloc[0]
    assert event.category == "data_issue" and event.rule_type == "flatline"
    assert event.ts_start == pd.Timestamp("2026-02-05 05:00")
    assert event.ts_end == pd.Timestamp("2026-02-07 23:00")
    assert pd.isna(event.excess_kwh) and pd.isna(event.est_cost_tl)
    assert event.explanation.startswith("2026-02-05 05:00-2026-02-08 00:00: Sayaç")
    assert "TL" not in event.explanation


def test_data_issues_further_apart_stay_separate():
    scored = on_grid(hours("2026-02-05 00:00", 8), hours("2026-02-07 00:00", 8))
    assert len(build_events(scored, tariff=4.0)) == 2


def test_waste_without_excess_is_dropped():
    below = hours(
        "2026-02-08 10:00",
        4,
        kwh=20.0,
        rule_score=0.0,
        source="ml_unsup",
        rule_type=None,
        rule_ratio=None,
    )
    tiny = hours("2026-02-09 01:00", 2, kwh=40.3, rule_type="night_base_load", rule_ratio=1.6)
    waste = hours("2026-02-10 01:00", 3, kwh=60.0, rule_type="night_base_load", rule_ratio=1.5)
    events = build_events(on_grid(below, tiny, waste), tariff=4.0)
    assert len(events) == 1
    event = events.iloc[0]
    assert event.category == "waste" and event.excess_kwh == 60.0
    assert event.est_cost_tl == 240.0


def test_off_hours_equipment_fires_at_a_smaller_excess():
    # Night load 1.4x the same hour in past weeks: waste for an air conditioner, not yet for a
    # whole-building meter.
    night = pd.DataFrame(
        {
            "log_kwh": np.log1p(14.0),
            "hw_median": np.log1p(10.0),
            "median_168": np.log1p(10.0),
            "z_168": 0.0,
            "flat_run": 0,
            "hour": 2,
            "is_weekend": 0,
            "is_holiday": 0,
        },
        index=[0, 1, 2],
    )
    out = evaluate(night, pd.Series(["klima", "genel", None]))
    assert out["rule_type"].tolist()[0] == "night_base_load"
    assert out["rule_type"].isna().tolist()[1:] == [True, True]
    assert evaluate(night)["rule_type"].isna().all()


def test_equipment_comes_from_the_catalog_unless_the_database_gave_it():
    buildings = pd.DataFrame({"external_id": ["Hog_industrial_Jay", "not-in-catalog"]})
    assert equipment_types(buildings).tolist()[0] == "plc"
    assert pd.isna(equipment_types(buildings).tolist()[1])
    given = buildings.assign(equipment_type=["klima", None])
    from_db = equipment_types(given).tolist()
    assert from_db[0] == "klima" and pd.isna(from_db[1])
