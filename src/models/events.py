"""Turn hourly alarms into anomaly events with excess kWh, TL cost and an explanation."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.common.runs import runs
from src.models.detector import SOURCES
from src.models.rules import FLATLINE_HOURS

MAX_GAP_HOURS = 1
EVENT_COLUMNS = [
    "building_id",
    "ts_start",
    "ts_end",
    "score",
    "source",
    "rule_type",
    "excess_kwh",
    "est_cost_tl",
    "explanation",
]
EMPTY_DTYPES = {
    "building_id": "int64",
    "ts_start": "datetime64[ns]",
    "ts_end": "datetime64[ns]",
    "score": "float64",
    "excess_kwh": "float64",
    "est_cost_tl": "float64",
}

# Explanations are Turkish, for the facility managers who read them; numbers use tr-TR style.
_TEMPLATES = {
    "night_base_load": "Gece tüketimi son haftalardaki aynı saatin {ratio} katına çıktı.",
    "weekend_usage": "Hafta sonu veya tatil tüketimi son haftalardaki aynı saatin {ratio} katı"
    " oldu.",
    "spike": "Tüketim haftalık medyanın {ratio} katına sıçradı.",
    "dropout": "Sayaç sıfıra yakın tüketim bildirdi; sayacı veya beslemeyi kontrol edin.",
    "flatline": "Sayaç en az {flat} saat aynı değeri gönderdi; takılmış olabilir.",
    "ml_sup": "Tüketim deseni geçmişte etiketlenmiş anomalilere benziyor.",
    "ml_unsup": "Tüketim beklenen seviyenin {ratio} katı oldu.",
}


def tr_number(value: float, digits: int = 0) -> str:
    """1234.5 -> '1.234,5' (Turkish separators)."""
    text = f"{value:,.{digits}f}"
    return text.replace(",", "_").replace(".", ",").replace("_", ".")


def explain(row: pd.Series, tariff: float) -> str:
    key = row["rule_type"] if row["source"] == "rule" else row["source"]
    # The rule fires on the FLATLINE_HOURS-th equal reading, so earlier equal hours count too.
    flat = row["flat_hours"] + FLATLINE_HOURS - 1
    text = _TEMPLATES[key].format(ratio=tr_number(row["ratio"], 1), flat=flat)
    window = f"{row['ts_start']:%Y-%m-%d %H:%M}-{row['ts_end'] + pd.Timedelta(hours=1):%H:%M}"
    cost = ""
    if row["excess_kwh"] > 0:
        cost = (
            f" Beklenenden yaklaşık {tr_number(row['excess_kwh'])} kWh fazla"
            f" ({tr_number(tariff, 2)} TL/kWh ile {tr_number(row['est_cost_tl'])} TL)."
        )
    return f"{window}: {text}{cost}"


def build_events(scored: pd.DataFrame, tariff: float) -> pd.DataFrame:
    """One row per alarm event, shaped like the `anomalies` table."""
    s = scored.sort_values(["building_id", "ts"], ignore_index=True)
    s["event"] = runs(s["alarm"].to_numpy(), s["building_id"].to_numpy(), max_gap=MAX_GAP_HOURS)
    hits = s[s["event"] >= 0].copy()
    if hits.empty:
        return pd.DataFrame(columns=EVENT_COLUMNS).astype(EMPTY_DTYPES)
    hits["excess"] = (hits["kwh"] - hits["expected_kwh"]).clip(lower=0).fillna(0)
    hits["score"] = np.maximum(hits["detector_score"], hits["rule_score"])
    hits["source_rank"] = hits["source"].map({s: i for i, s in enumerate(SOURCES)})
    hits["flat"] = hits["rule_type"].eq("flatline")

    g = hits.groupby("event")
    events = g.agg(
        building_id=("building_id", "first"),
        ts_start=("ts", "min"),
        ts_end=("ts", "max"),
        score=("score", "max"),
        excess_kwh=("excess", "sum"),
        kwh=("kwh", "sum"),
        expected=("expected_kwh", "sum"),
        source_rank=("source_rank", "min"),
        flat_hours=("flat", "sum"),
    )
    events["source"] = events["source_rank"].map(dict(enumerate(SOURCES)))
    fired = hits.dropna(subset=["rule_type"])
    top_rule = fired.loc[
        fired.groupby("event")["rule_score"].idxmax(), ["event", "rule_type", "rule_ratio"]
    ]
    events = events.join(top_rule.set_index("event"))
    # A rule that fired without raising the alarm does not name a model event.
    events["rule_type"] = events["rule_type"].where(events["source"] == "rule")
    events["hours"] = ((events["ts_end"] - events["ts_start"]) / pd.Timedelta(hours=1)).astype(
        int
    ) + 1
    model_ratio = events["kwh"] / events["expected"].clip(lower=1e-6)
    events["ratio"] = events["rule_ratio"].where(events["source"] == "rule", model_ratio)
    events["est_cost_tl"] = events["excess_kwh"] * tariff
    events["explanation"] = events.apply(explain, axis=1, tariff=tariff)
    return events[EVENT_COLUMNS].reset_index(drop=True)
