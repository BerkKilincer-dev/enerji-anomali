# Energy Anomaly Detection

Finds anomalies in hourly electricity meter data of commercial buildings, estimates their cost
in TL and explains them in plain language. SofITo AI training capstone project (Project 14).

The system learns on public data (LEAD 1.0 + Building Data Genome 2 weather and metadata) and
is built for real customers. Tenants register their buildings with a segment, location and
operating hours; meter exports or MQTT messages flow into a shared Postgres; a scoring service
turns new hours into alarms; and the dashboard shows them with cost and a feedback button that
becomes training labels.

## Quick start

Local stack (Postgres + TimescaleDB, MQTT, API, dashboard):

```bash
cp .env.example .env          # set the three passwords, API_KEY and the password in DATABASE_URL
docker compose up -d --build
```

| Service | Address |
|---|---|
| Dashboard | http://localhost:5173 |
| API docs | http://localhost:8000/docs |
| Postgres | `localhost:5432`, database `energy`, schemas `core`, `telemetry`, `ml`, `ops` |
| MQTT | `localhost:1883` |

Load the public dataset into the database once (owner role from `.env`):

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m src.data.load_db                          # downloads, prepares and loads
```

Live scoring and MQTT ingest need a trained model in `models_out/` (see below):

```bash
docker compose --profile live up -d
```

Without a database the API serves built-in demo data, so the dashboard always has something
to show (`uvicorn src.api.main:app --reload`, then `cd frontend && npm install && npm run dev`).

## Data and ML in Colab

| Notebook | What it does | Runtime |
|---|---|---|
| [`notebooks/01_data_pipeline.ipynb`](notebooks/01_data_pipeline.ipynb) | download, prepare, split, quality report, optional DB load | CPU, ~5 min |
| [`notebooks/02_ml_pipeline.ipynb`](notebooks/02_ml_pipeline.ipynb) | train and compare all models, evaluate, save model and reports | CPU, ~20-40 min |

Both clone the repository with the Colab secret `GITHUB_TOKEN` while it is private. Everything
the notebooks do is also available from the command line:

```bash
python -m src.data.download && python -m src.data.prepare && python -m src.data.quality
python -m src.models.train            # --quick for a smoke run, --source db to train on the database
```

## Repository

| Path | Content | Owner |
|---|---|---|
| `src/data` | sources, preparation, split, DB loading, tenants and buildings, imports, weather API | Data engineer |
| `src/features` | past-only features and holiday calendar | Data + ML |
| `src/models` | rules, detectors, training, evaluation, reports, model registry | ML developer |
| `src/scoring` | live scoring service | ML + Backend |
| `src/ingest` | MQTT ingest and a virtual meter for demos | Data + Backend |
| `src/api` | FastAPI app and data access | Backend |
| `frontend` | React dashboard | Frontend |
| `db/init` | database schema in `core`, `telemetry`, `ml` and `ops` | Data + Backend |
| `notebooks` | Colab entry points for data and ML | Data + ML |
| `reports` | committed data quality and ML results | Data + ML |
| `tests` | unit and database integration tests | Everyone |
| `docs` | architecture, guides, decisions, roadmap | Everyone |

## Documentation

[Architecture](docs/architecture.md) · [Data](docs/data.md) ·
[Machine learning](docs/machine-learning.md) · [API](docs/api.md) ·
[Backend](docs/backend.md) · [Frontend](docs/frontend.md) · [Security](docs/security.md) ·
[Roadmap](docs/roadmap.md) · [Decisions](docs/decisions.md) · [Handover](docs/handover.md) ·
[Commercial readiness](docs/commercial-readiness.md)

## Workflow

```bash
git checkout main && git pull
git checkout -b feature/short-description
pytest -q && ruff check .
git push -u origin feature/short-description   # then open a PR for review
```

- No direct pushes to `main`; every change goes through a reviewed PR. Keep PRs small.
- Never commit `data/raw`, `data/processed`, `models_out/`, `.env` or any password or key.
- Everyone uses the committed building split (`data/splits.json`) and seed 42.
- Branch names: `feature/...`, `fix/...`, `docs/...`.
