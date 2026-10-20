"""Optional MLflow tracking; every call is a no-op when MLflow is unavailable."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from src.config import get_settings

log = logging.getLogger(__name__)
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
EXPERIMENT = "energy-anomaly"


class Tracker:
    def __init__(self, mlflow_module=None):
        self._mlflow = mlflow_module

    def params(self, values: dict) -> None:
        if self._mlflow:
            self._mlflow.log_params({k: str(v)[:250] for k, v in values.items()})

    def metrics(self, values: dict, prefix: str = "") -> None:
        if self._mlflow:
            clean = {
                f"{prefix}{k}": float(v) for k, v in values.items() if isinstance(v, int | float)
            }
            self._mlflow.log_metrics(clean)

    def artifacts(self, path: Path, folder: str) -> None:
        if self._mlflow and path.exists():
            self._mlflow.log_artifacts(str(path), artifact_path=folder)


def use_store(mlflow) -> None:
    """MLflow reads MLFLOW_TRACKING_URI itself; without it, SQLite and artifacts in MLFLOW_DIR."""
    settings = get_settings()  # loads .env first, so a tracking URI there is seen too
    if not os.getenv("MLFLOW_TRACKING_URI"):
        store = settings.mlflow_dir
        store.mkdir(parents=True, exist_ok=True)
        mlflow.set_tracking_uri(f"sqlite:///{store / 'mlflow.db'}")
        if mlflow.get_experiment_by_name(EXPERIMENT) is None:
            mlflow.create_experiment(EXPERIMENT, (store / "artifacts").as_uri())
    mlflow.set_experiment(EXPERIMENT)


def _import_mlflow():
    try:
        import mlflow
    except ImportError:
        log.info("mlflow not installed, tracking disabled")
        return None
    return mlflow


@contextmanager
def run(name: str, enabled: bool = True) -> Iterator[Tracker]:
    mlflow = _import_mlflow() if enabled else None
    if mlflow is None:
        yield Tracker()
        return
    use_store(mlflow)
    with mlflow.start_run(run_name=name):
        yield Tracker(mlflow)
