# Security

The stack is built to run safely on one machine for development and demos. This page lists
what is already in place, what must change before customers use it, and the rules every
contributor follows.

## In place

| Area | Measure | Where |
|---|---|---|
| Secrets | passwords and keys only in `.env` (git-ignored) and Colab secrets; `.env.example` has empty values | `.gitignore`, `.env.example` |
| Network | every compose port is bound to `127.0.0.1`; services talk over the compose network | `docker-compose.yml` |
| Database roles | owner for schema changes, `team` read and write on data, `reader` read only; services run as `team` | `src/data/load_db.py` |
| Passwords | role passwords need at least 12 characters; `load_db` refuses shorter ones | `load_db.ensure_role` |
| SQL | parameterized queries everywhere; identifiers and role names go through `psycopg.sql` | `src/` |
| API writes | `X-API-Key` compared in constant time (`hmac.compare_digest`); writes are off when `API_KEY` is unset | `src/api/main.py` |
| Input limits | uploads at most 20 MB, `hours` at most 2160, `limit` at most 500, validated body fields and timezones | `src/api/main.py`, `schemas.py` |
| CORS | explicit origin list (`CORS_ORIGINS`), only `GET` and `POST`, only two headers | `src/api/main.py` |
| Model files | LightGBM text and JSON, no pickle, so loading a bundle cannot run code | `src/models/registry.py` |
| Downloads | public datasets are checked against pinned SHA-256 checksums | `src/data/sources.py` |
| Containers | slim base image, runs as non-root user `app` (uid 10001), model directory mounted read-only | `Dockerfile` |
| Alerts | the Telegram bot token lives only in `.env`; a message carries the building name, time range, kWh and TL estimate, nothing else | `src/scoring/alerts.py` |
| Colab | the GitHub token is read from Colab secrets, never printed and removed from the git remote after cloning | notebooks |

## Before production

1. **TLS everywhere**: a reverse proxy (Caddy or nginx) in front of the API and dashboard,
   `sslmode=require` for Postgres, an MQTT TLS listener on 8883.
2. **MQTT authentication**: `allow_anonymous false`, a `password_file` and an ACL so a
   device can publish only to `meters/<its own external_id>`; set `MQTT_USER` and
   `MQTT_PASSWORD` for the ingest service.
3. **User accounts instead of one API key**: the dashboard keeps the key in `localStorage`,
   which any script running on the page can read. Customers need a login, a server-side session
   in an HttpOnly cookie, roles (viewer, operator, admin) and a tenant id on every query.
4. **Tenant isolation**: today every user sees every building. Add `customer_id` to
   `buildings` and filter every query by it, ideally with Postgres row-level security.
5. **Rate limits** on the API (per key and per IP), and a body size limit in the proxy so large
   uploads are refused before they reach the app.
6. **Secrets management**: Docker secrets or a vault; rotate the database passwords and the
   API key, and never reuse the development values.
7. **Backups**: daily `pg_dump` or continuous WAL archiving, with a tested restore.
8. **Audit**: log who registered buildings, imported files and gave feedback (the
   `created_by` field is free text today).
9. **Dependency checks**: `pip-audit` and `npm audit` in CI; pin image tags instead of
   `latest`.
10. **Personal data (KVKK)**: meter data of a home or a small office can reveal when people are
    present. Keep a processing agreement with customers, store only what is needed and define a
    retention period.

## Rules for contributors

- Never commit `.env`, passwords, API keys, tokens, customer files, `data/raw`,
  `data/processed` or `models_out/`. If a secret is pushed, rotate it; deleting the commit is
  not enough.
- Never build SQL with f-strings or string concatenation from input; use parameters, and
  `psycopg.sql` for identifiers.
- Validate every new API field in `schemas.py` with a type and a limit.
- Do not log readings of real customers, passwords or keys.
- Use the `reader` role for analysis and notebooks; use the owner only for schema changes.
- Do not open new ports on `0.0.0.0` in compose.
- Report a vulnerability privately to the repository owner, not in a public issue.
