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
| Asset and configuration | `src.data.customer`, `POST /buildings`, schedule endpoints | `core.buildings`, `core.operating_hours`, `core.planned_downtime`, `core.assets`, `core.meters` | buildings, schedules and the meter catalog (`src.data.catalog`) in use |
| Data ingestion | `src.ingest.mqtt_ingest`, CSV import | writes `telemetry.readings` | in place |
| Telemetry | readings and forecast endpoints | `telemetry.readings`, `telemetry.weather` | in place |
| AI / anomaly | `src.scoring.service`, `src.models` | `ml.*`: `predictions`, `anomalies`, `scoring_state`, `model_artifacts`, view `readings_enriched` | in place |
| Identity and tenant | `src.api.auth`, `src.api.users`, `/auth/*`, `/users`, `POST /tenants` | `core.tenants`, `core.users`, `core.sessions`, `buildings.tenant_id` | login, roles and tenant scoping in place |
| Analytics and reporting | `GET /summary`, `GET /tenants/summary` | views `ops.daily_kpis`, `ml.current_predictions`, `ml.current_anomalies` | in place |
| Recommendation | `src.api.recommendations`, `GET /recommendations`, `recommendation` on each anomaly | `data/recommendations.csv`; status in `ml.anomaly_feedback` (`sent`, `applied`) | in place |
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
- The follow-up of an alarm is its latest verdict in `anomaly_feedback`: `sent` and `applied`
  for the recommendation, `confirmed`, `false_alarm` and `planned` for the alarm. The same
  table is the label source for training.
- `daily_kpis` sums kWh, expected kWh and excess per building and day over valid hours;
  partial and merged hours count as missing, as in the models.
- Reloading LEAD (`load_db --force`) also removes schedules, assets and meters of LEAD
  buildings; customer buildings are never touched.

## Running locally

With Docker:

```bash
cp .env.example .env              # fill in every password
docker compose up -d --build      # db, mqtt, init, setup, api, dashboard, scoring, ingest, live-demo
docker compose logs -f api scoring ingest live-demo
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

The scoring service sends no messages; new events show up in the dashboard. A notification
channel (e-mail, SMS) would need its own table of sent events, so each event goes out once.

## LLM advice

With `ANTHROPIC_API_KEY` set, every scoring cycle ends with `src.scoring.advice.write_advice`.
It picks live events that ended at least 6 hours before the building's last scored hour and
within the last 7 days, costliest first, at most 10 per cycle, and asks Claude (`LLM_MODEL`,
default `claude-opus-5-5`, low effort) for at most three Turkish sentences: the likely cause
and one concrete action. The fixed prompt is `SYSTEM_PROMPT` in that module; the request
carries only the facts the dashboard shows. The text lands in `anomalies.advice`; a declined
request stores an empty string so it is not asked again, and a network or rate-limit error
stops the batch until the next cycle. The log line `llm advice on` confirms it is enabled.

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

### Live demo (panel button)

`src.ingest.live_demo` runs as the compose service `live-demo`. At start-up it registers
"Canlı Demo Tesisi", its five meters from `data/live_meters.csv` (LEAD validation buildings,
which neither the catalog nor training uses), their assets and the user
`canlidemotesisi@enerji.local`, then polls the one-row table `ops.live_demo` every second and
writes a heartbeat there.

- `POST /live-demo/start` (platform admin) sets `running` and increments `run_id`. A new
  `run_id` makes the service delete the live meters' readings, predictions, anomalies,
  feedback and scoring state, then send the backlog: at least four weeks ending at fast
  start, the latest 22:00 at least a day back.
- After the backlog it sends one hour every `LIVE_DEMO_SECONDS_PER_HOUR` seconds (60 by
  default) until it reaches the current hour, then each hour once it has ended. Hours go out
  one at a time with a 20 ms pause; Mosquitto queues up to 20,000 messages per client.
- Each meter has one problem: `stuck` 24 hours and `spike` (5x) 10 hours before fast start, so
  they are in the backlog and show at once; `dropout` one hour and `night_base_load` at fast
  start, so they show within minutes. `drift` is left out: it needs days to raise an alarm.
- `POST /live-demo/stop` clears `running`; the data stays. `GET /live-demo` returns the switch,
  `available` (heartbeat within 30 s), the last hour sent (`sim_ts`) and each live meter's last
  reading.
- The scoring service runs every 20 seconds, so alarms follow fast play closely. The LEAD
  history of the other meters is untouched: scoring skips LEAD buildings.

### Replay tool

`src.ingest.replay` publishes LEAD buildings as live meters on `meters/<prefix>-<building>`.
Timestamps are shifted by whole weeks so the backlog (`--days`, 28 by default) ends at the
current hour and weekdays still line up. Every hour carries all replayed buildings together;
backlog hours go out every `--speed` seconds (0.2 by default, about two minutes for four
weeks), later hours when they end in real time. `--register` registers the buildings first;
add `--tenant <business>` (for example `--tenant Otel`) so they belong to a business, since
the panel lists only meters that belong to one.
Ctrl+C stops the replay; running the same command again resends the backlog, which is
harmless because readings are upserted.

`--inject <scenario>` rewrites one building (`--inject-building`, the first one by default)
from two days before now, or later for short scenarios, so the anomaly is already in the
backlog and still running when the replay catches up. `--magnitude` sets the multiplier for
`spike`, `drift` and `night_base_load` (3 by default for night load). Without `--inject` the
data is replayed unchanged.

Demo script:

```bash
docker compose up -d
python -m src.ingest.replay --building 1 2 3 --register --tenant Otel \
    --inject night_base_load --inject-building 2 --magnitude 2.5
```

If an event does not show up, check each hop:
`readings` rows with `source = 'mqtt'` (ingest), `predictions` and `anomalies` with
`origin = 'live'` (scoring), then the dashboard.

## Adding an endpoint

1. Model in `schemas.py`.
2. Method on the `Repository` protocol and both implementations.
3. Route in `main.py`; write routes use `dependencies=Writer`.
4. A test in `tests/test_api.py` (demo mode) and, for SQL, in `tests/test_db.py`.
5. The client in `dashboard/api_client.py` and the table in [api.md](api.md).

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
