# API

FastAPI app in `src/api/main.py`. Interactive docs at `http://localhost:8000/docs` and the
OpenAPI schema at `/openapi.json`. Request and response bodies are defined in
`src/api/schemas.py`; the frontend mirrors them in `frontend/src/api.ts`.

```bash
uvicorn src.api.main:app --reload        # demo data without DATABASE_URL
```

## Modes

| `DATABASE_URL` | Mode | Reads | Writes |
|---|---|---|---|
| set | `database` | Postgres | allowed with `X-API-Key` |
| not set | `demo` | two built-in Istanbul buildings | 503 |

Timestamps are the building's local time without an offset, for example
`2026-10-19T01:00:00`.

## Read endpoints

No key needed.

| Method and path | Query | Returns |
|---|---|---|
| `GET /health` | | `{"status": "ok", "mode": "demo" \| "database"}`; `mode` tells which repository serves the data |
| `GET /buildings` | | active buildings with `last_scored_ts` and `anomaly_count` |
| `GET /buildings/{id}/readings` | `hours` 1-2160, default 48 | hourly `kwh` and label `anomaly` |
| `GET /buildings/{id}/forecast` | `hours` 1-2160, default 48 | `expected_kwh`, `alarm`, `source`, `origin` |
| `GET /anomalies` | `building_id` optional, `limit` 1-500, default 50 | events, newest first, with the latest `verdict` (feedback whose event was re-published still applies by time range) |
| `GET /summary` | | `total_cost_tl`, `active_alerts`, `worst_building_id`, `buildings` |

- The `hours` window ends at the building's latest reading, not at the current time, so a
  building that stopped sending data still shows its last period.
- Once the scoring service covers a building, `forecast`, `anomalies`, `anomaly_count` and
  `summary` use its live output only; before that they show the batch output from training.
- `active_alerts` counts events that ended within 24 hours of the building's latest reading.
- `summary` covers active buildings only, the same set as `GET /buildings`.

```bash
curl "http://localhost:8000/buildings/100000/readings?hours=24"
curl "http://localhost:8000/anomalies?building_id=100000&limit=10"
```

## Write endpoints

Every write needs the header `X-API-Key: <API_KEY>`. Without `API_KEY` on the server, writes
are switched off and return 503.

### `POST /buildings`

Registers a building, or updates it when `external_id` already exists. Returns 201 and
`{"id": <building_id>}`.

| Field | Rule |
|---|---|
| `external_id` | required, `[A-Za-z0-9._-]`, 1-64 characters |
| `name` | up to 200 characters |
| `lat`, `lng` | valid coordinates; missing means `DEFAULT_LAT` and `DEFAULT_LNG` (Istanbul) |
| `timezone` | IANA name such as `Europe/Istanbul`; missing means `DEFAULT_TIMEZONE` |
| `country` | two letters; derived from the timezone when missing |
| `primary_use` | up to 100 characters, for example `Office` |
| `square_feet` | greater than 0 |
| `year_built` | 1800-2100 |
| `tariff_tl_per_kwh` | 0 or more; missing means `TARIFF_TL_PER_KWH` |

```bash
curl -X POST http://localhost:8000/buildings \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"external_id": "acme-hq", "name": "Acme HQ", "lat": 41.01, "lng": 28.98,
       "primary_use": "Office", "square_feet": 54000, "tariff_tl_per_kwh": 4.2}'
```

### `POST /buildings/{id}/readings/import`

Uploads a meter export as `multipart/form-data`. The cleaning rules are in
[data.md](data.md#import-a-meter-export).

| Form field | Default | Meaning |
|---|---|---|
| `file` | required | CSV export, at most 20 MB |
| `ts_col` | `timestamp` | timestamp column |
| `value_col` | `kwh` | value column |
| `unit` | `kwh` | `wh`, `kwh`, `mwh` or `kbtu` |
| `cumulative` | `false` | `true` for a register that only counts up |
| `source_tz` | building timezone | timezone of naive timestamps |
| `sep` | sniffed | column separator |
| `decimal` | `.` | `,` for Turkish exports |

Returns the import report: `rows_in`, `unparseable`, `duplicates`, `conflicting_duplicates`,
`negative`, `resets`, `gap_diffs`, `future`, `step_minutes`, `hours_out`, `partial_hours`,
`first_ts`, `last_ts`.

```bash
curl -X POST http://localhost:8000/buildings/100000/readings/import \
  -H "X-API-Key: $API_KEY" \
  -F file=@export.csv -F ts_col=Tarih -F value_col=Tuketim -F decimal=,
```

### `POST /anomalies/{id}/feedback`

Stores an operator verdict. Returns 201 and `{"id": <feedback_id>}`. Verdicts become training
labels (see [data.md](data.md#feedback-as-labels)).

| Field | Rule |
|---|---|
| `verdict` | `confirmed`, `false_alarm` or `planned` |
| `note` | up to 1000 characters |
| `created_by` | up to 100 characters |

```bash
curl -X POST http://localhost:8000/anomalies/1/feedback \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"verdict": "confirmed", "note": "chiller left on", "created_by": "ops"}'
```

## Errors

Errors return `{"detail": "..."}`.

| Status | When |
|---|---|
| 401 | `X-API-Key` missing or wrong |
| 404 | building or anomaly not found, or a building without readings |
| 413 | upload larger than 20 MB |
| 422 | invalid body or query, or an export that cannot be parsed |
| 503 | `API_KEY` not set, or a write in demo mode |

## CORS

Allowed origins come from `CORS_ORIGINS` (comma separated, default
`http://localhost:5173,http://127.0.0.1:5173`). Methods `GET` and `POST`, headers
`Content-Type` and `X-API-Key`.

## Changing the API

1. Add or change the model in `src/api/schemas.py`.
2. Add the query to `Repository`, `PostgresRepository` and `DemoRepository` in
   `src/api/repository.py`.
3. Add the route in `src/api/main.py`; writes get `dependencies=Writer`.
4. Update the types in `frontend/src/api.ts`, this file and `tests/test_api.py`.
