"""Turkish recommendation for each anomaly, by equipment type and anomaly type.

The texts live in data/recommendations.csv so they can be edited without touching code. A row
with `*` as equipment type is the fallback for every equipment; `*,*` is the last resort.
Anomaly types are the rule names (night_base_load, weekend_usage, spike, dropout, flatline) and
`model` for events the ML models found without a firing rule.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

from src.config import ROOT

RECOMMENDATIONS_PATH = ROOT / "data" / "recommendations.csv"
ANY = "*"
MODEL = "model"
COLUMNS = ("equipment_type", "anomaly_type", "title", "action")


def anomaly_type(rule_type: str | None) -> str:
    return rule_type or MODEL


@lru_cache(maxsize=1)
def table(path: Path = RECOMMENDATIONS_PATH) -> dict[tuple[str, str], dict]:
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return {(r["equipment_type"], r["anomaly_type"]): {k: r[k] for k in COLUMNS} for r in rows}


def recommend(equipment_type: str | None, rule_type: str | None) -> dict:
    """The most specific row: this equipment and type, any equipment and type, then `*,*`."""
    kind = anomaly_type(rule_type)
    texts = table()
    for key in ((equipment_type or ANY, kind), (ANY, kind), (ANY, ANY)):
        if key in texts:
            return {"title": texts[key]["title"], "action": texts[key]["action"]}
    raise LookupError(f"{RECOMMENDATIONS_PATH.name} has no '{ANY},{ANY}' row")
