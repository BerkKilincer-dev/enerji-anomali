# Dashboard

Owner: frontend developer. The Streamlit CRM panel in `dashboard/`: a business signs in, sees
its meters, their consumption against the expected level, the anomalies with their TL cost and
advice, gives feedback on each alarm and exports reports. It reads everything through the API
([api.md](api.md)); the session cookie stays on the Streamlit server, never in the browser.
The sidebar picks a sayaç (meter); some older cards still say bina (building).

| Item | Choice |
|---|---|
| Framework | Streamlit (pinned in `dashboard/requirements.txt`) |
| Charts | matplotlib, rendered as images |
| Styling | `dashboard/style.css` plus inline CSS; it targets Streamlit's own class names, so upgrade Streamlit only on purpose and check every page |
| Exports | CSV, Excel (openpyxl), PDF (matplotlib) in `dashboard/reports.py` |

## Running

`docker compose up -d --build` starts it as the `dashboard` service on http://localhost:5173.
The folder is mounted into the container and Streamlit polls for changes, so an edited file
shows after a browser refresh; a new package in `requirements.txt` needs `--build`.

## Structure

| File | Role |
|---|---|
| `app.py` | landing page (one screen about Wattwise; sign-in from the "Giriş yap" popover top right), sidebar and the pages: İşletmeler, Genel Bakış, Tüketim Analizi, Anomaliler, Raporlar, Ayarlar (account, weekly operating hours, planned downtime; editing needs operator or admin) |
| `api_client.py` | API client; one `requests.Session` per signed-in user keeps the cookie |
| `charts.py` | consumption and donut charts, Turkish date formats |
| `reports.py` | report data and the CSV, Excel and PDF exports |
| `icons.py` | SVG icons and the logo |
| `style.css` | look and feel |

## CRM flow

- **Genel Bakış** is the first item in the menu and opens after sign-in.
- **İşletmeler**: platform staff see one card per business (`GET /tenants/summary`)
  with waste, open alarms and data issues for 30 days, 90 days (default, the LEAD data ends in a
  quiet December) or a year. A business user goes straight to its own page.
- **İşletme**: the same totals, "Bugün ne yapmalı?" (the three costliest open waste alarms with
  their recommendation) and the meters sorted by location; "Aç" opens a meter in Genel Bakış.
- **Anomaliler**: filters for scope, status, equipment and order, served by `GET /anomalies`;
  waste and data issues sit in separate tabs. Each alarm shows its recommendation card.
- **Canlı İzleme**: the live demo (`GET /live-demo`). Platform admins start, restart and stop
  it; everyone else only watches. The screen refreshes every 10 seconds inside a fragment and
  reads with `fresh=True`, past the client's 30-second cache. It shows the five live meters
  with their last reading and an Alarm / Veri sorunu / Normal badge, a 48-hour chart and the
  newest alarms with their status buttons. The live business gets a "Canlı" badge on İşletmeler.
- Status buttons (operator and admin): İletildi (`sent`), Uygulandı (`applied`), Yanlış alarm
  (`false_alarm`), Planlı (`planned`). Applied, false alarm and planned close an alarm. The
  detail page also takes an optional note. Button colours come from `style.css` by key prefix
  (`d_<code>_` on the detail page, `<code>_` in lists), so keep those keys when moving buttons.

## Conventions

- Every API call goes through `api_client.py`.
- Costs come from the API: anomalies carry `est_cost_tl`; consumption costs use each meter's
  `tariff_tl_per_kwh` from `GET /buildings`. `TARIFF_TL` in `app.py` is only the fallback.
- Anomalies with `category == "data_issue"` (stuck or silent meter) have no cost; show them
  apart from waste.
- Timestamps from the API are building local time without an offset; never shift them.
- Cached loaders take `user_id` in their key, so two users never share data.
- `Api` caches each GET for 30 s (`CACHE_TTL`) per signed-in user. Any POST, PUT or DELETE clears
  that user's cache, so a write shows at once. Every response is a fresh copy.
- `api_client` logs each failed call and each call slower than 1 s to `docker compose logs dashboard`,
  and keeps the last 100 such calls in memory. Admins see them in Ayarlar → Sistem günlüğü.
  A network failure is raised as `ApiError(0, ...)`, so pages show a message, not a traceback.
- Read many buildings with `readings_paralel` (6 at a time), not a loop.
- CI runs `app.py` headless and checks that the login page renders.
- Accessibility (WCAG AA):
  - Draw charts with `fig_html(fig, kind, alt)`, not `st.pyplot`, so the image carries alt text.
    Time-series charts also get `grafik_tablosu`, the same data as a table.
  - Text and badge colours need at least 4.5:1 contrast. White text goes on `#DC2626`, `#B45309`,
    `#047857`, `#2563EB` or `#64748B`, not on the lighter Tailwind 500 tones.
  - Never show state by colour alone; badges carry a word, chart bands have a legend and a label.
  - SVG icons are decorative (`aria-hidden`); put the meaning in the text next to them.
