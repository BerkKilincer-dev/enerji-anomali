# Models

Owner: ML developer. Guide: [docs/machine-learning.md](../../docs/machine-learning.md).

| Module | Role |
|---|---|
| `train.py` | end-to-end training and evaluation (`python -m src.models.train`) |
| `detector.py` | `AnomalyScorer`, the single inference path |
| `rules.py` | explainable rules with severities in [0, 1] |
| `supervised.py` | LightGBM and Random Forest detectors |
| `unsupervised.py` | expected-consumption model, residual score, Isolation Forest |
| `boosting.py` | shared LightGBM datasets and early stopping, reused by tuning trials |
| `events.py` | alarm hours into costed, explained events |
| `metrics.py` | PR-AUC, event metrics, alarm rates, bootstrap intervals |
| `report.py` | tables and figures in `reports/ml/` |
| `registry.py` | model bundle save, load and publish (LightGBM text + JSON) |
| `publish.py` | batch predictions and events into the database |
| `tracking.py` | optional MLflow logging |

Rules: building-level split from `data/splits.json`, seed 42, thresholds chosen on
validation buildings only.
