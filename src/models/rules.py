"""Explainable rule engine: night base load, off-day usage, spike, dropout, flatline.

Every rule returns a severity in [0, 1] and fires at severity >= 0.5, so one
threshold works for all rules and the max severity doubles as a ranking score.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.common.runs import runs
from src.features.calendar import NIGHT_HOURS

FIRE_AT = 0.5
DAYTIME_HOURS = range(8, 21)
OFF_HOURS_RATIO = 1.5  # off-hours load above 1.5x the same hour in past weeks
# Equipment that should be off outside working hours: a smaller off-hours excess is already
# waste. Cooling and whole-building meters run around the clock and keep the default; every
# rule compares a meter with its own past, so their normal night load never fires.
OFF_HOURS_EQUIPMENT = ("klima", "aydinlatma", "plc", "kompresor")
STRICT_OFF_HOURS_RATIO = 1.3
SPIKE_Z = 4.0  # and above 2x the weekly median
SPIKE_RATIO = 2.0
DROPOUT_RATIO = 0.1  # below 10% of the weekly median
MIN_BASE_KWH = 1.0
FLATLINE_HOURS = 6  # identical readings in a row
MIN_WASTE_HOURS = 2  # one odd off-hours reading is noise, not waste
RULES = ("night_base_load", "weekend_usage", "spike", "dropout", "flatline")
WASTE_RULES = ("night_base_load", "weekend_usage")


def _ratio(log_value: pd.Series, log_base: pd.Series) -> pd.Series:
    return np.expm1(log_value) / np.expm1(log_base).clip(lower=MIN_BASE_KWH)


def _scaled(value: pd.Series, fire_at: float | pd.Series) -> pd.Series:
    return (value / (2 * fire_at)).clip(0, 1).fillna(0)


def evaluate(features: pd.DataFrame, equipment: pd.Series | None = None) -> pd.DataFrame:
    """Per-rule severities, `rule_score` (max), `rule_type` (firing rule or None) and
    `rule_ratio` (load versus the baseline that rule compares against).

    `equipment` is the equipment type per row (core.assets.equipment_type) when known."""
    f = features
    off_ratio = _ratio(f["log_kwh"], f["hw_median"])
    off_limit = pd.Series(OFF_HOURS_RATIO, index=f.index)
    if equipment is not None:
        strict = np.isin(np.asarray(equipment, dtype=object), OFF_HOURS_EQUIPMENT)
        off_limit = off_limit.mask(strict, STRICT_OFF_HOURS_RATIO)
    weekly_ratio = _ratio(f["log_kwh"], f["median_168"])
    has_base = np.expm1(f["median_168"]) >= MIN_BASE_KWH

    night = f["hour"].isin(NIGHT_HOURS)
    off_day = (f["is_weekend"].eq(1) | f["is_holiday"].eq(1)) & f["hour"].isin(DAYTIME_HOURS)
    spike = np.minimum(f["z_168"] / SPIKE_Z, weekly_ratio / SPIKE_RATIO)
    drop = 1 - weekly_ratio / (2 * DROPOUT_RATIO)

    out = pd.DataFrame(index=f.index)
    out["night_base_load"] = _scaled(off_ratio, off_limit).where(night, 0.0)
    out["weekend_usage"] = _scaled(off_ratio, off_limit).where(off_day, 0.0)
    out["spike"] = (spike / 2).clip(0, 1).fillna(0)
    out["dropout"] = drop.clip(0, 1).where(has_base, 0.0).fillna(0)
    flat = f["flat_run"] + 1
    out["flatline"] = _scaled(flat, FLATLINE_HOURS).where(f["log_kwh"] > 0, 0.0)
    out[list(RULES)] = out[list(RULES)].astype("float32")

    out["rule_score"] = out[list(RULES)].max(axis=1)
    top = out[list(RULES)].idxmax(axis=1)
    out["rule_type"] = top.where(out["rule_score"] >= FIRE_AT)
    ratio = np.where(top.isin(WASTE_RULES), off_ratio, weekly_ratio)
    out["rule_ratio"] = pd.Series(ratio, index=f.index).where(out["rule_type"].notna())
    out["rule_ratio"] = out["rule_ratio"].astype("float32")
    return out


def persistent(hits: np.ndarray, rule_type: pd.Series, building: np.ndarray) -> np.ndarray:
    """Drop waste-rule hits that do not last MIN_WASTE_HOURS consecutive hours."""
    waste = hits & rule_type.isin(WASTE_RULES).to_numpy()
    run = runs(waste, building)
    if run.max() < 0:
        return hits
    long_enough = np.bincount(run[run >= 0])[np.maximum(run, 0)] >= MIN_WASTE_HOURS
    return hits & (~waste | long_enough)
