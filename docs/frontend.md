# Frontend

Owner: frontend developer. The React CRM panel in `frontend/`: a business sees its meters,
their anomalies with the TL cost and the advice, and gives feedback on each alarm. It reads
the API ([api.md](api.md)). Turkish by default, English from the switch in the header. The
code says `building` where the panel says meter (sayaç), see the README.

| Item | Choice |
|---|---|
| Build | Vite 8, TypeScript 7 (strict) |
| UI | React 19, plain CSS (`src/styles.css`) |
| Charts | ECharts 6, tree-shaken imports, loaded lazily on the building page |
| Routing | URL hash: `#/buildings/<id>` |
| State | `useAsync` hook per request, no global store |
| Language | `src/i18n.tsx`, `tr` and `en` dictionaries, choice kept in `localStorage` |
| Login | session cookie set by the API; the page never sees a token |

## Running

```bash
cd frontend
cp .env.example .env        # VITE_API_URL, default http://localhost:8000
npm install
npm run dev                 # http://127.0.0.1:5173
npm run build               # type check and production bundle in dist/
```

The API without `DATABASE_URL` serves demo data and needs no login, so the dashboard works
without a database; a "demo verisi" tag appears in the header. With a database the dashboard
opens on the login form; `.env.example` creates `admin@enerji.local` on first start, more users
come from `python -m src.api.users create` (see [api.md](api.md#login-roles-and-tenants)). In Docker Compose the `frontend` service runs the
dev server.

## Structure

| File | Role |
|---|---|
| `src/api.ts` | typed client; types mirror `src/api/schemas.py`; sends the cookie and `X-Requested-With` |
| `src/i18n.tsx` | `LangProvider`, `useLang()` with `t(key)`, `lang` and `setLang` |
| `src/App.tsx` | header with language and sign-out, login gate, routes |
| `src/components/Login.tsx` | e-mail and password form |
| `src/components/Overview.tsx` | summary cards and the meter table |
| `src/components/BuildingDetail.tsx` | one meter: range picker, chart, anomaly table |
| `src/components/EnergyChart.tsx` | actual against expected kWh, alarm periods shaded; created once and updated when data changes |
| `src/components/AnomalyTable.tsx` | events with cost, explanation, Claude advice when present, and feedback buttons |
| `src/format.ts` | TL, kWh and time formatting, rule labels |
| `src/useAsync.ts` | data, error and reload for one request |
| `src/components/Async.tsx` | shows loading, the error or the loaded content of a `useAsync` state |

## Conventions

- All API calls go through `src/api.ts`; change the types there whenever the backend schema
  changes.
- Every visible string goes through `t()`. Add the key to both dictionaries in `src/i18n.tsx`;
  TypeScript fails the build when the English one misses a key.
- Timestamps from the API are building local time without an offset. Show them as they are
  and never convert them with the browser timezone.
- Money is shown in TL with `tr-TR` formatting, energy in kWh.
- Show write controls by role (`canWrite(me)`, `me.role === "admin"`); the API checks again.
- Keep components small and typed; no `any`.
- Run `npm run build` before you push; CI runs it again on every push.

## Phones

Below 640 px the padding shrinks, the cards go two per row and the chart is 300 px high. Tables sit in `.table-wrap` and scroll sideways instead of
widening the page; timestamps (`.nowrap`) stay on one line. Check a change at 375 px width
(browser device mode): the page must not scroll horizontally.
