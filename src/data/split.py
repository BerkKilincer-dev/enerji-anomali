"""Building-level train/val/test split shared by the whole team.

Usage: python -m src.data.split [--force]
The split lives in data/splits.json and is committed; never split by rows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split

from src.config import SEED, SPLITS_PATH, get_settings
from src.data.prepare import Tables

SPLITS = ("train", "val", "test")
VAL_SIZE = 30
TEST_SIZE = 30
RATE_BINS = 4
HASH_SHARES = (("train", 0.70), ("val", 0.85), ("test", 1.0))


@dataclass(frozen=True)
class Split:
    train: list[int]
    val: list[int]
    test: list[int]

    def as_series(self) -> pd.Series:
        rows = [(b, name) for name in SPLITS for b in getattr(self, name)]
        return pd.Series(dict(rows), name="split").rename_axis("building_id")


def make_split(readings: pd.DataFrame, seed: int = SEED) -> Split:
    # Stratify on anomaly-rate quartiles so every split sees similar label density.
    rate = readings[readings["kwh"].notna()].groupby("building_id")["anomaly"].mean()
    bins = pd.qcut(rate, RATE_BINS, labels=False, duplicates="drop")
    train, rest = train_test_split(
        rate.index.to_numpy(), test_size=VAL_SIZE + TEST_SIZE, stratify=bins, random_state=seed
    )
    val, test = train_test_split(
        rest, test_size=TEST_SIZE, stratify=bins.loc[rest], random_state=seed
    )
    return Split(*(sorted(int(b) for b in part) for part in (train, val, test)))


def hashed_split(key: str) -> str:
    """Stable split for buildings outside splits.json, such as new customers."""
    bucket = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 0x1_0000_0000
    return next(name for name, upper in HASH_SHARES if bucket < upper)


def assign_splits(buildings: pd.DataFrame, split: Split | None = None) -> pd.Series:
    """An explicit split wins, then the stored column, then splits.json, then a stable hash."""
    committed = buildings["building_id"].map((split or load_split()).as_series())
    if split is None and "split" in buildings:
        committed = buildings["split"].where(buildings["split"].notna(), committed)
    keys = buildings["external_id"] if "external_id" in buildings else buildings["building_id"]
    return committed.fillna(keys.astype(str).map(hashed_split)).rename("split")


def save_split(split: Split, path: Path = SPLITS_PATH, seed: int = SEED) -> None:
    strategy = f"building-level, stratified by anomaly-rate quartile ({RATE_BINS} bins)"
    lines = [f'  "seed": {seed}', f'  "strategy": {json.dumps(strategy)}']
    lines += [f'  "{name}": {json.dumps(getattr(split, name))}' for name in SPLITS]
    path.write_text("{\n" + ",\n".join(lines) + "\n}\n")


def load_split(path: Path = SPLITS_PATH) -> Split:
    payload = json.loads(path.read_text())
    split = Split(*(payload[name] for name in SPLITS))
    ids = [*split.train, *split.val, *split.test]
    if len(set(ids)) != len(ids):
        raise ValueError("a building appears in more than one split")
    return split


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="overwrite the committed split")
    args = parser.parse_args()
    if SPLITS_PATH.exists() and not args.force:
        split = load_split()
        print(f"keeping existing {SPLITS_PATH}")
    else:
        split = make_split(Tables.load(get_settings().processed_dir).readings)
        save_split(split)
        print(f"wrote {SPLITS_PATH}")
    print({name: len(getattr(split, name)) for name in SPLITS})


if __name__ == "__main__":
    main()
