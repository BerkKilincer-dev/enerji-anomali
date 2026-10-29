# Machine learning

Owner: ML developer. Code in `src/models` and `src/features`, entry point
`notebooks/02_ml_pipeline.ipynb` (same steps as `python -m src.models.train`).

## Problem

Hourly binary detection per building: is this hour anomalous? The product turns alarm hours
into events with excess kWh, TL cost and an explanation. Two kinds of anomaly matter:

- **Meter and data faults**: dropouts, zero readings, stuck meters, spikes. LEAD labels are
  mostly this kind.
- **Energy waste**: night base load, weekend and holiday usage, slow drift. Rare in LEAD, so it
  is covered by rules and evaluated with synthetic injections.

## Features

`src/features/build_features.py`, past-only (hour t uses t and earlier), shared by training
and live scoring.

| Group | Examples |
|---|---|
| Calendar | hour, day of week, weekend, public holiday per country, Bayram eve |
| Weather | air and dew temperature, wind, cloud cover, heating and cooling degree hours (18 °C), weekly mean temperature, temperature anomaly |
| Building | log floor area, year built, primary use |
| Baseline | median of the same hour in past weeks (`hw_median`), same hour on past days, 7 and 28 day medians, weekly mean and std |
| Recent | current log kWh, lags 1-3, 24, 168, differences, 24 h stats, z-scores, residual to baselines, level ratio, zero flag, flatline length, missing hours in 24 h |

The expected-consumption model gets calendar, weather, building and baseline features only:
no current value and no short lags, so a long anomaly cannot pull its own expectation up.

## Models

All trained on train buildings; thresholds picked on validation buildings; numbers reported on
test buildings.

| Model | Family | Role |
|---|---|---|
| Expected consumption | LightGBM L1 regression on log kWh, normal hours only | expected kWh for cost and the residual detector |
| Rules | severities in [0, 1], fire at 0.5 | explainable alarms, waste detection |
| LightGBM | supervised classifier, early stopping on validation | main detector |
| LightGBM without weather | ablation | shows the weather contribution |
| LightGBM hybrid | detector plus expected and residual features | candidate main detector |
| Random Forest | supervised baseline | sanity check for LightGBM |
| Isolation Forest | unsupervised | label-free baseline |
| Expected residual | \|residual\| over the building's recent residual spread | label-free detector for new and unlabeled buildings |

The LightGBM variant with the best validation PR-AUC becomes the scorer's detector, so the
test numbers stay unbiased. Its threshold is the best F1 threshold on validation. The
residual threshold allows one alarm event per building per week on validation.

## Product alarm

`AnomalyScorer.score` (`src/models/detector.py`) is the single inference path:

1. rule hit (severity ≥ 0.5); waste rules (night base load, weekend usage) must last at least
   2 consecutive hours, a single odd reading is not reported,
2. otherwise detector score ≥ threshold,
3. otherwise residual score ≥ threshold.

Off-hours rules (night and weekend load) fire at 1.3x the same hour in past weeks instead of
1.5x for equipment that should be off outside working hours (`klima`, `aydinlatma`, `plc`,
`kompresor` in `core.assets.equipment_type`, or `data/meter_catalog.csv` for file runs);
cooling and whole-building meters keep 1.5x. Rules start after 7 days of history in the
scored frame, models after 14 days. Alarm hours
with gaps of at most one hour form one event (`src/models/events.py`). Each event is
`waste` (TL cost) or `data_issue` (flatline or dropout, no cost). Data issues of one rule on
one meter less than 24 hours apart merge into one event; waste events under 1 kWh above
expected are dropped, which also removes alarms on consumption below the expected level
(the residual score is two-sided). Model metrics are computed on hourly alarms and do not
change with these event rules.

Live scoring passes `CONTEXT_HOURS` (4 weeks for the longest feature plus 2 weeks for the
residual scale) before the new hours, so it gives the same scores as a full rescore. It also
rewrites the last scored hour (`REVISION_HOURS`), because the 2-hour waste rule can only fire
once the next hour has arrived. `CONTEXT_HOURS` follows from `HISTORY_HOURS` in
`build_features.py`, which must cover the longest lookback of any feature.

## Evaluation

- Ranking: PR-AUC (main metric, labels are rare) and ROC-AUC, with a 95% bootstrap interval
  over test buildings.
- At threshold: hourly precision, recall, F1; event recall and event precision; alarms and false
  alarms per building-week (what an operator actually feels).
- Alarm budget table: thresholds for 0.5 to 10 alarms per building-week, chosen on validation
  and applied to test, to pick the operating point with the customer.
- Synthetic scenarios: spike, dropout, stuck meter, 2-week drift and a week of night base load
  injected into clean windows of every test building; detection rate, hourly recall and hours
  to detect. Per-detector rows use raw scores; the `combined` row is the product alarm.
- Expected model: MAE, median APE and WAPE on normal test hours.
- Day level: a building-day is anomalous if any hour is, as in the LEAD paper. Its benchmark
  table and why the numbers are not directly comparable:
  [docs/rapor/lead-benchmark.md](rapor/lead-benchmark.md).

Results: [reports/ml/results.md](../reports/ml/results.md), from the full run on all
buildings (30 held-out test buildings). Rerun and commit it after every model change.

Observations from the full run:

- LightGBM is the best detector (test PR-AUC about 0.77); the hybrid is on par; dropping
  weather costs a little. Random Forest is close but slower; unsupervised models are far
  behind on LEAD labels.
- The top feature is flatline length, then short differences: LEAD labels are dominated by
  stuck and dropping meters.
- The combined product alarm has low event precision against LEAD labels (about 0.2) because
  most product alarms are waste rules, and LEAD does not label waste. Feedback labels are the
  real measure.
- Spikes, dropouts, stuck meters and night base load are detected in nearly every injection;
  slow drift is mostly missed.

## Retraining with real data

1. Customers' readings and operator feedback accumulate in the database.
2. `python -m src.models.train --source db --publish` trains on every building in the
   database, with feedback verdicts as labels (confirmed 1, false alarm and planned 0).
3. `--publish` stores the bundle in `model_artifacts` and the test predictions and events as
   `batch`; the scoring service picks up the newest bundle on its next cycle. Notebook 02
   publishes only with `PUBLISH = True`, so a smoke run never replaces the live model.

Supervised models only ever see labeled hours, so unlabeled customer hours never become
negatives by accident.

## Running

| Mode | Command | Time |
|---|---|---|
| Full | `python -m src.models.train` or notebook 02 | ~20-40 min on a Colab CPU |
| Smoke | `--quick` | a few minutes |
| Tuning | `--tune 50` (Optuna on the hybrid model) | longer |
| Tracking | every run is logged to MLflow (below); `--no-mlflow` turns it off | |

No GPU or TPU is needed: the data fits in memory and LightGBM trains on CPU.

MLflow writes to `MLFLOW_TRACKING_URI` when it is set (a shared tracking server), otherwise to
a SQLite store with artifacts in `MLFLOW_DIR` (default `mlruns/`, git-ignored). Notebook 02
puts `MLFLOW_DIR` on Google Drive when `USE_DRIVE` is on, so runs survive the Colab runtime.
The CLI and the notebook log through the same `train.save_run`: params, metrics, reports and
the bundle. View the runs with `mlflow ui --backend-store-uri sqlite:///mlruns/mlflow.db`.
`mlflow` is in the Colab and dev requirements only; without it tracking is skipped.

Outputs: `models_out/` (bundle: `expected.txt`, `detector.txt`, `bundle.json`, committed so the
team needs no training; `python -m src.models.publish` loads it into a database)
and `reports/ml/` (tables, figures, `results.md`, committed).

## Known limitations and next steps

- Drift: add a weekly CUSUM on the residual or compare against the same week last year.
- Holidays and school terms: education buildings need term calendars, not only public holidays.
- Thresholds per segment: a hospital and an office tolerate different alarm rates.
- Calibration: probabilities are not calibrated; the score is only used for ranking and
  thresholds.
- Weather in training comes from BDG2 stations, live weather from Open-Meteo; check the gap on
  real customers before trusting weather-driven alarms.
