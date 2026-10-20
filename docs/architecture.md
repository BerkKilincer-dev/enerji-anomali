# Architecture

## Overview

```
 public data                 customers
 LEAD 1.0 + BDG2         CSV exports      MQTT meters/<external_id>
      │                       │                    │
 src.data.prepare     POST /readings/import   src.ingest.mqtt_ingest
      │               src.data.customer            │
      ▼                       ▼                    ▼
 ┌──────────────────────── Postgres (TimescaleDB optional) ─────────────────────────┐
 │ tenants · users · buildings · assets · meters · readings · weather ·             │
 │ predictions · anomalies · anomaly_feedback · scoring_state · model_artifacts ·   │
 │ alert_settings · notifications · work_orders · views current_*, daily_kpis       │
 └──────────────────────────────────────────────────────────────────────────────────┘
      │  ▲                         │  ▲                                   ▲
      │  │ Open-Meteo weather      │  │ predictions, anomalies           │ feedback
      ▼  │ (src.data.weather_api)  ▼  │                                   │
 src.models.train ──bundle──▶ src.scoring.service            src.api.main (FastAPI)
 (Colab or local)            (every 5 min, new hours)                 │
                                                                       ▼
                                                              frontend (React)
```

Everything that turns readings into alarms goes through one class,
`src.models.detector.AnomalyScorer`. Training, offline evaluation, the scoring service and the
demo call the same `score` method, so they cannot drift apart.

## Components

The mapping to the target services (identity, asset, ingestion, telemetry, anomaly, alert,
reporting, work order) and the tables each one owns is in
[backend.md](backend.md#service-map).

| Component | Module | Runs as |
|---|---|---|
| Data pipeline | `src.data.download`, `prepare`, `split`, `quality`, `load_db` | CLI or notebook 01 |
| Customer onboarding | `src.data.customer`, `validate`, `weather_api` | CLI or API |
| Features | `src.features.build_features`, `calendar` | library |
| Training | `src.models.train` | CLI or notebook 02 |
| Model registry | `src.models.registry` | files in `models_out/` + table `model_artifacts` |
| Live scoring | `src.scoring.service` | container `scoring` (profile `live`) |
| MQTT ingest | `src.ingest.mqtt_ingest` | container `ingest` (profile `live`) |
| Alerts | `src.scoring.alerts` | inside the scoring service, Telegram when configured |
| Virtual meters | `src.ingest.replay` | demo CLI: replays LEAD buildings over MQTT, optional injected anomaly |
| API | `src.api.main`, `repository`, `schemas` | container `api` |
| Dashboard | `frontend/` | container `frontend` or `npm run dev` |

## Scoring pipeline

1. **Features** (`build_features`): past-only. Calendar and public holidays per country
   (Turkish Bayram eves count from 13:00), weather and degree hours, building metadata, robust
   baselines (same hour in past weeks, weekly medians), recent lags, differences, z-scores,
   flatline length and missing-data counters.
2. **Expected consumption**: LightGBM regression on normal hours, trained without the current
   value or short lags so a long anomaly does not leak into its own baseline.
3. **Rules** (`rules.py`): night base load, weekend/holiday usage, spike, dropout, flatline.
   Every rule gives a severity in [0, 1] and fires at 0.5. Waste rules must last 2 hours.
4. **Detector**: LightGBM classifier trained on labeled hours (LEAD labels and operator
   feedback). The residual against expected consumption is a label-free second detector.
5. **Alarm**: rule, then detector, then residual, each with its own threshold. New buildings
   wait a week before rules and two weeks before models fire.
6. **Events** (`events.py`): consecutive alarm hours (1-hour gaps merged) become one event with
   excess kWh, TL cost from the building tariff and a one-sentence explanation.

## Data design

- `buildings.building_id` is internal: LEAD keeps its BDG2 ids, customers start at 100000.
  `external_id` is the customer's own meter or site id and is unique.
- `readings` hold hourly kWh in building local time with `quality` (ok, partial, merged,
  missing) and `source`. `anomaly` is the label: LEAD label or operator feedback, else NULL.
- `weather` is keyed by `weather_key`: a BDG2 site name for public data, `geo:<lat>,<lng>`
  rounded to 2 decimals for customers, so nearby buildings share one weather series.
- `predictions` and `anomalies` carry `origin`: `batch` (published from training) or `live`
  (scoring service). Once a building is scored live, the API reads only its live output
  (`current_predictions`, `current_anomalies`).
- `scoring_state` stores the last scored hour per building, so scoring is incremental.
- `model_artifacts` stores model bundles, so the scoring service never needs a file share.
- `tenants`, `users`, `assets`, `meters` and `work_orders` are ready for the identity,
  asset and work order services; `notifications` and `alert_settings` drive the alerts.
- TimescaleDB hypertables are created when the extension exists; plain tables otherwise.

## Deployment

`docker-compose.yml` runs the whole stack on one machine, every port bound to `127.0.0.1`.
One Python image serves `init`, `api`, `scoring` and `ingest`; `init` applies the schema and
creates roles, then exits. Production needs TLS, MQTT authentication and secrets management,
see [security.md](security.md).

## Conventions

- Python 3.11+, ruff (line length 100), type hints, dataclasses for settings and results.
- Configuration only from environment variables (`src.config.get_settings`).
- No row-level splits: train/val/test are building sets (`data/splits.json`).
- Timestamps in the database and features are naive local time of the building; MQTT and
  CSV imports convert offsets and DST before storing.
