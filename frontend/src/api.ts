// Typed client for the backend; shapes mirror src/api/schemas.py.

export const API_URL = (import.meta.env.VITE_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
const KEY_STORAGE = "energy-anomaly.api-key";

export type Building = {
  building_id: number;
  external_id: string;
  name: string | null;
  source: string;
  primary_use: string | null;
  square_feet: number | null;
  timezone: string;
  country: string | null;
  lat: number | null;
  lng: number | null;
  last_scored_ts: string | null;
  anomaly_count: number;
};

export type Reading = { building_id: number; ts: string; kwh: number | null; anomaly: number | null };

export type ForecastPoint = {
  building_id: number;
  ts: string;
  expected_kwh: number | null;
  alarm: boolean;
  source: string | null;
  origin: string;
};

export type Verdict = "confirmed" | "false_alarm" | "planned";

export type Anomaly = {
  id: number;
  building_id: number;
  ts_start: string;
  ts_end: string;
  score: number | null;
  source: string | null;
  rule_type: string | null;
  excess_kwh: number | null;
  est_cost_tl: number | null;
  explanation: string | null;
  origin: string;
  verdict: Verdict | null;
};

export type Summary = {
  total_cost_tl: number;
  active_alerts: number;
  worst_building_id: number | null;
  buildings: number;
};

export type Health = { status: string; mode: "demo" | "database" };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : response.statusText;
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

export const getApiKey = () => localStorage.getItem(KEY_STORAGE) ?? "";
export const setApiKey = (key: string) =>
  key ? localStorage.setItem(KEY_STORAGE, key) : localStorage.removeItem(KEY_STORAGE);

export const api = {
  health: () => request<Health>("/health"),
  summary: () => request<Summary>("/summary"),
  buildings: () => request<Building[]>("/buildings"),
  readings: (id: number, hours: number) => request<Reading[]>(`/buildings/${id}/readings?hours=${hours}`),
  forecast: (id: number, hours: number) => request<ForecastPoint[]>(`/buildings/${id}/forecast?hours=${hours}`),
  anomalies: (id: number, limit = 50) => request<Anomaly[]>(`/anomalies?building_id=${id}&limit=${limit}`),
  feedback: (anomalyId: number, verdict: Verdict) =>
    request<{ id: number }>(`/anomalies/${anomalyId}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": getApiKey() },
      body: JSON.stringify({ verdict }),
    }),
};
