# Backend

Owner: backend developer. Four Python services share one image (`Dockerfile`) and one
database. Endpoint reference: [api.md](api.md).

| Service | Module | Compose service | What it does |
|---|---|---|---|
| Schema init | `src.data.load_db --schema-only` | `init` | applies `db/init/001_schema.sql`, creates the team and reader roles, exits |
| API | `src.api.main` | `api` | dashboard reads, building registration, CSV import, feedback |
| Scoring | `src.scoring.service` | `scoring` (profile `live`) | scores new hours every 5 minutes, writes predictions and events |
| Ingest | `src.ingest.mqtt_ingest` | `ingest` (profile `live`) | MQTT meter messages into `readings` |

## Service map

The target architecture has the services below. They run today as a modular monolith: one
codebase, one Postgres, three processes (API, scoring, ingest). Each service owns its tables
and other services only read them, so a service can move into its own process later without a
schema rewrite. Tables are grouped by area in the Postgres schemas `core`, `telemetry`, `ml`
and `ops` ([data.md](data.md#shared-database)).

| Service | Code today | Tables it owns | Status |
|---|---|---|---|
| API gateway / BFF | `src.api.main` | none | in place; a reverse proxy with TLS goes in front before production |
| Identity and tenant | `POST /tenants`, `src.data.customer tenant` | `core.tenants`, `core.users`, `buildings.tenant_id` | tenant registration in place; login, roles and tenant filtering planned |
| Asset and configuration | `src.data.customer`, `POST /buildings`, schedule endpoints | `core.buildings`, `core.operating_hours`, `core.planned_downtime`, `core.assets`, `core.meters`, `ops.alert_settings` | buildings and schedules in use; asset, meter and settings endpoints planned |
| Data ingestion | `src.ingest.mqtt_ingest`, CSV import | writes `telemetry.readings` | in place |
| Telemetry | readings and forecast endpoints | `telemetry.readings`, `telemetry.weather` | in place |
| AI / anomaly | `src.scoring.service`, `src.models` | `ml.*`: `predictions`, `anomalies`, `scoring_state`, `model_artifacts`, view `readings_enriched` | in place |
| Alert | `src.scoring.alerts` | `ops.notifications` | Telegram in place; email and a chat per tenant planned |
| Analytics and reporting | `GET /summary` | views `ops.daily_kpis`, `ml.current_predictions`, `ml.current_anomalies` | KPI view in place; report endpoints planned |
| Work order | none | `ops.work_orders`, `ml.anomaly_feedback` | schema ready; endpoints planned |
| Message broker | Mosquitto | none | MQTT for meters; Kafka or RabbitMQ only when services split |

Model of the data behind it:

- A tenant is one customer company with its tax, contact, plan and contract details; a user
  with no tenant is platform staff. Roles are
  `admin`, `operator` and `viewer`. Once login exists, every API query filters buildings by
  the user's tenant (Postgres row-level security is the stricter option).
- A building is one site (factory, office, store) with a fixed `segment` and an optional
  weekly schedule (`operating_hours`) and planned downtime. `assets` holds its lines and machines as a
  tree; `meters` registers its physical meters, at most one main meter. `readings` stores the
  main meter, which is what the models score; submeter readings get their own table when a
  customer needs them.
- `alert_settings` holds per-building thresholds: `notify` mutes a building and `min_cost_tl`
  drops cheap events. Buildings without a row get every event.
- A work order tracks the follow-up of an alarm (open, in progress, resolved, dismissed).
  `anomaly_feedback` stays the label source for training; closing a work order should also
  write feedback (resolved as `confirmed`, dismissed as `false_alarm`).
- `daily_kpis` sums kWh, expected kWh and excess per building and day over valid hours;
  partial and merged hours count as missing, as in the models.
- Reloading LEAD (`load_db --force`) also removes schedules, assets, meters, alert settings
  and work orders of LEAD buildings; customer buildings are never touched.

## Running locally

With Docker:

```bash
cp .env.example .env              # fill in every password and API_KEY
docker compose up -d --build      # db, mqtt, init, api, frontend
docker compose --profile live up -d
docker compose logs -f api scoring ingest
```

Without Docker (Postgres and an MQTT broker reachable from your machine):

```bash
pip install -r requirements-dev.txt
python -m src.data.load_db --schema-only          # owner URL in DATABASE_URL
uvicorn src.api.main:app --reload                  # API on :8000
python -m src.scoring.service --once               # one scoring cycle
python -m src.ingest.mqtt_ingest                   # MQTT consumer
python -m src.ingest.replay --split test --register  # virtual meters for demos
```

Services read configuration only from environment variables (`src/config.py`), with `.env`
loaded when `python-dotenv` is installed. Services run as the `team` role; only `load_db`
needs the owner.

## API layers

- `main.py`: routes, validation limits, key check, error mapping. No SQL.
- `repository.py`: the `Repository` protocol with two implementations. `PostgresRepository`
  runs parameterized SQL; `DemoRepository` returns fixed Istanbul data and raises
  `ReadOnlyError` on writes, so the frontend works without a database.
- `schemas.py`: Pydantic request and response models.

Domain errors map to HTTP in one place: `NotFoundError` becomes 404, `ConflictError` 409 and
`ReadOnlyError` 503.
Imports and registration call the same functions as the CLI (`src.data.customer`), so the
cleaning rules exist only once.

## Scoring service

Each cycle:

1. Uses the newest bundle in `model_artifacts`, falling back to `MODEL_DIR` at start-up. Each
   cycle checks the newest version and switches when a new model was published.
2. Finds active customer buildings with readings after `scoring_state.last_scored_ts`
   (`--include-lead` adds LEAD buildings).
3. Per building: fills missing Open-Meteo weather from the last scored day on (skip it with
   `--no-weather`; a failed call is logged and scoring goes on without it). Then, in one
   transaction, it fetches the new hours plus 1009 hours of context
   (`CONTEXT_HOURS + REVISION_HOURS`, more while an event is open), runs `AnomalyScorer.score`,
   upserts `predictions` with `origin = 'live'`, upserts events and moves `last_scored_ts`
   forward.
4. A failing building is logged and retried next cycle; the others continue. The whole cycle
   uses one database connection.

The context covers the longest feature lookback (4 weeks) plus the residual scale window
(2 weeks), so new hours get the same scores as a full rescore of the building; this is
checked in `tests/test_scoring.py` and `tests/test_db.py`. The last scored hour is rewritten
as well, because a waste hit needs the next hour to confirm it.

Events are keyed by `(building_id, ts_start, origin)`. When a live event may still be
running, the context starts that much earlier, before the event's own start, so the event keeps
its start, cost and text even when it lasts longer than the context. A cycle that runs twice
does not duplicate anything.

Once a building has a `scoring_state` row, the API reads its live output only
(`current_predictions` and `current_anomalies` views); before that it shows the batch output
published by training.

## Alerts

With `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` set, every scoring cycle ends with
`src.scoring.alerts.send_alerts`. It sends each live event once (`notifications`, one row per
event and channel), only while the event ended in the last 24 hours of the building's local
time, so a replayed backlog or a scoring outage does not flood the chat. Events are filtered
by `alert_settings`, sent costliest first and at most 10 per cycle. A failed send stops the
batch and is retried next cycle; an event that keeps running is not sent again.

Setup: create a bot with [@BotFather](https://t.me/BotFather), send it a message, read the
chat id from `https://api.telegram.org/bot<token>/getUpdates`, put both values in `.env` and
restart the scoring service. The log line `telegram alerts on` confirms it.

Known limits:

- Readings that arrive late for hours that were already scored are stored but not rescored.
- Weather for recent days comes from the Open-Meteo forecast API and is not replaced by
  archive values later.

## Shared helpers

- `src/data/store.py`: table column lists, `fetch_tables` (database rows as `Tables`, the
  same shape as the processed files), `copy_frame` for bulk loads and `upsert_frame`
  (COPY into a temporary table, then `INSERT ... ON CONFLICT DO UPDATE`) for every write
  that can repeat. `upsert_frame` must run inside a transaction.
- `src/ingest/broker.py`: MQTT client from `MQTT_HOST`, `MQTT_PORT`, `MQTT_USER` and
  `MQTT_PASSWORD`, shared by the ingest service and the virtual meter.
- `src/config.py`: settings from the environment; `require_database_url()` stops a CLI with a
  clear message when `DATABASE_URL` is missing.

## MQTT ingest

Subscribes to `meters/+` with QoS 1. Each message is parsed, converted to the building's local
hour and buffered; every 5 seconds the buffer is upserted per building with
`source = 'mqtt'`. Messages for unknown buildings, malformed payloads and timestamps more than
an hour in the future are dropped and logged; a negative or non-finite value is stored as a
missing hour. Buildings are looked up on every flush, so a changed timezone applies without a
restart. A failed database write puts the batch back into the buffer, which keeps at most
100,000 readings and drops the oldest beyond that.

## Virtual meters and the live demo

`src.ingest.replay` publishes LEAD buildings as live meters on `meters/<prefix>-<building>`.
Timestamps are shifted by whole weeks so the backlog (`--days`, 28 by default) ends at the
current hour and weekdays still line up. Every hour carries all replayed buildings together;
backlog hours go out every `--speed` seconds (0.2 by default, about two minutes for four
weeks), later hours when they end in real time. `--register` registers the buildings first.
Ctrl+C stops the replay; running the same command again resends the backlog, which is
harmless because readings are upserted.

`--inject <scenario>` rewrites one building (`--inject-building`, the first one by default)
from two days before now, or later for short scenarios, so the anomaly is already in the
backlog and still running when the replay catches up. `--magnitude` sets the multiplier for
`spike`, `drift` and `night_base_load` (3 by default for night load). Without `--inject` the
data is replayed unchanged.

Demo script:

```bash
docker compose --profile live up -d
python -m src.ingest.replay --building 1 2 3 --register \
    --inject night_base_load --inject-building 2 --magnitude 2.5
```

Run the scoring service with a short `--interval` (for example 30) during a demo, so the
event shows up within a minute after the backlog ends. Check each hop if it does not:
`readings` rows with `source = 'mqtt'` (ingest), `predictions` and `anomalies` with
`origin = 'live'` (scoring), then `notifications` (Telegram). A second cycle without new
readings sends nothing.

## Adding an endpoint

1. Model in `schemas.py`.
2. Method on the `Repository` protocol and both implementations.
3. Route in `main.py`; write routes use `dependencies=Writer`.
4. A test in `tests/test_api.py` (demo mode) and, for SQL, in `tests/test_db.py`.
5. Types in `frontend/src/api.ts` and the table in [api.md](api.md).

## Database changes

`db/init/001_schema.sql` is the only schema file and must stay idempotent
(`IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`). Every table and view is created with its
schema prefix (`core.`, `telemetry.`, `ml.`, `ops.`; `tests/test_schema.py` checks it); a new
schema also goes into `SCHEMAS` in `src/common/db.py`, which feeds the connection
`search_path` and the role grants. New tables get grants for the team and reader roles
through the default privileges set by `load_db`; check them in `tests/test_db.py`.
`apply_schema` also runs `ALTER DATABASE ... SET search_path`, so it must run as the
database owner. A value list that may grow (segments) is a named check that the file drops and
adds again; `tests/test_schema.py` checks the lists against the Python literals.

## Tests

```bash
pytest -q                                          # unit tests, DB tests skip
TEST_DATABASE_URL=postgresql://owner:...@localhost:5432/energy_test pytest -q
```

`TEST_DATABASE_URL` must point to a database used only for tests: every test creates its own
database next to it and drops it afterwards. Use a role that may create databases and roles.
