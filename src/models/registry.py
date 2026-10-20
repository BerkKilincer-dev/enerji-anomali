"""Save and load the scorer bundle as LightGBM text + JSON (no pickle)."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import lightgbm as lgb
from psycopg.types.json import Jsonb

from src.common.db import connect
from src.models.detector import AnomalyScorer, Thresholds

BUNDLE_NAME = "anomaly-scorer"
FILES = {"expected": "expected.txt", "detector": "detector.txt"}


def save(scorer: AnomalyScorer, out_dir: Path, metrics: dict | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    for part, name in FILES.items():
        getattr(scorer, part).save_model(out_dir / name)
    meta = {
        "name": BUNDLE_NAME,
        "version": scorer.version,
        "thresholds": asdict(scorer.thresholds),
        "metrics": metrics or {},
    }
    (out_dir / "bundle.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return out_dir


def _from_parts(meta: dict, expected: str, detector: str) -> AnomalyScorer:
    return AnomalyScorer(
        expected=lgb.Booster(model_str=expected),
        detector=lgb.Booster(model_str=detector),
        thresholds=Thresholds(**meta["thresholds"]),
        version=meta["version"],
    )


def _read_bundle(model_dir: Path) -> tuple[dict, str, str]:
    meta = json.loads((model_dir / "bundle.json").read_text(encoding="utf-8"))
    expected, detector = ((model_dir / name).read_text(encoding="utf-8") for name in FILES.values())
    return meta, expected, detector


def load(model_dir: Path) -> AnomalyScorer:
    return _from_parts(*_read_bundle(model_dir))


def publish(model_dir: Path, database_url: str) -> str:
    """Store the bundle in the shared database so services can pull it."""
    meta, expected, detector = _read_bundle(model_dir)
    with connect(database_url) as conn:
        conn.execute(
            "INSERT INTO model_artifacts (name, version, meta, expected_model, detector_model) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (name, version) DO UPDATE SET "
            "meta = EXCLUDED.meta, expected_model = EXCLUDED.expected_model, "
            "detector_model = EXCLUDED.detector_model, created_at = now()",
            (BUNDLE_NAME, meta["version"], Jsonb(meta), expected, detector),
        )
    return meta["version"]


def _newest(database_url: str, columns: str) -> tuple | None:
    query = "SELECT {} FROM model_artifacts WHERE name = %s ORDER BY created_at DESC LIMIT 1"
    with connect(database_url) as conn:
        return conn.execute(query.format(columns), (BUNDLE_NAME,)).fetchone()


def fetch_latest(database_url: str) -> AnomalyScorer:
    row = _newest(database_url, "meta, expected_model, detector_model")
    if row is None:
        raise LookupError("no published model in model_artifacts")
    return _from_parts(*row)


def latest_version(database_url: str) -> str | None:
    """Version of the newest published bundle, without loading the models."""
    row = _newest(database_url, "version")
    return row[0] if row else None
