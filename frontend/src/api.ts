// Typed client for the backend; shapes mirror src/api/schemas.py.
// The session lives in an HttpOnly cookie, so this code never sees a token.

export const API_URL = (import.meta.env.VITE_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export type Role = "admin" | "operator" | "viewer";

export type Me = { user_id: number; tenant_id: number | null; email: string; name: string | null; role: Role };

export type Building = {
  building_id: number;
  external_id: string;
  name: string | null;
  source: string;
  primary_use: string | null;
  segment: string | null;
  square_feet: number | null;
  timezone: string;
  country: string | null;
  lat: number | null;
  lng: number | null;
  tenant_id: number | null;
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
  advice: string | null;
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
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = init.method ?? "GET";
  const headers = new Headers(init.headers);
  // The API refuses writes without this header, which cross-site forms cannot send.
  if (method !== "GET") headers.set("X-Requested-With", "dashboard");
  const response = await fetch(`${API_URL}${path}`, { ...init, headers, credentials: "include" });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : response.statusText;
    throw new ApiError(response.status, detail);
  }
  return (response.status === 204 ? undefined : response.json()) as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  health: () => request<Health>("/health"),
  me: () => request<Me>("/auth/me"),
  login: (email: string, password: string) => request<Me>("/auth/login", json("POST", { email, password })),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  summary: () => request<Summary>("/summary"),
  buildings: () => request<Building[]>("/buildings"),
  readings: (id: number, hours: number) => request<Reading[]>(`/buildings/${id}/readings?hours=${hours}`),
  forecast: (id: number, hours: number) => request<ForecastPoint[]>(`/buildings/${id}/forecast?hours=${hours}`),
  anomalies: (id: number, limit = 50) => request<Anomaly[]>(`/anomalies?building_id=${id}&limit=${limit}`),
  feedback: (anomalyId: number, verdict: Verdict) =>
    request<{ id: number }>(`/anomalies/${anomalyId}/feedback`, json("POST", { verdict })),
};

export const canWrite = (me?: Me) => me?.role === "admin" || me?.role === "operator";
