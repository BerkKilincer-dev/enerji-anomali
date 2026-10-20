"""Tiny helpers for writing markdown/JSON reports without extra dependencies."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


def _cell(value: object) -> str:
    if isinstance(value, float | np.floating):
        return "" if math.isnan(value) else f"{value:,.4g}"
    return str(value)


def md_table(df: pd.DataFrame, index: bool = False) -> str:
    frame = df.reset_index() if index else df
    head = "| " + " | ".join(map(str, frame.columns)) + " |"
    sep = "|" + "|".join("---" for _ in frame.columns) + "|"
    rows = [
        "| " + " | ".join(_cell(v) for v in row) + " |" for row in frame.itertuples(index=False)
    ]
    return "\n".join([head, sep, *rows])


def _jsonable(value: object) -> object:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating | float):
        return None if math.isnan(value) else round(float(value), 6)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(payload), indent=2) + "\n")
