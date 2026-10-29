import os

import requests

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Api:
    """Oturum cookie'sini requests.Session ile saklar."""

    def __init__(self):
        self.s = requests.Session()

    def _req(self, method, path, **kw):
        headers = {"X-Requested-With": "dashboard"} if method != "GET" else {}
        r = self.s.request(method, f"{API_URL}{path}", headers=headers, timeout=15, **kw)
        if not r.ok:
            try:
                detail = r.json().get("detail", r.reason)
                if isinstance(detail, list):  # 422 doğrulama hataları liste döner
                    detail = "; ".join(d.get("msg", str(d)) if isinstance(d, dict) else str(d) for d in detail)
            except Exception:
                detail = r.reason
            raise ApiError(r.status_code, str(detail))
        return None if r.status_code == 204 else r.json()

    def login(self, email, password):
        return self._req("POST", "/auth/login", json={"email": email, "password": password})

    def logout(self):
        return self._req("POST", "/auth/logout")

    def health(self):
        return self._req("GET", "/health")

    def summary(self):
        return self._req("GET", "/summary")

    def buildings(self):
        return self._req("GET", "/buildings")

    def readings(self, bid, hours, end=None):
        # end: pencerenin son saati (bina yerel saati, ISO); yoksa son okumada biter.
        params = {"hours": hours, **({"end": end} if end else {})}
        return self._req("GET", f"/buildings/{bid}/readings", params=params)

    def forecast(self, bid, hours, end=None):
        params = {"hours": hours, **({"end": end} if end else {})}
        return self._req("GET", f"/buildings/{bid}/forecast", params=params)

    def anomalies(self, bid, limit=50):
        return self._req("GET", "/anomalies", params={"building_id": bid, "limit": limit})

    def operating_hours(self, bid):
        return self._req("GET", f"/buildings/{bid}/operating-hours")

    def set_operating_hours(self, bid, periods):
        # periods: [{"weekday": 1-7, "opens": "HH:MM", "closes": "HH:MM"}]; boş liste takvimi siler.
        return self._req("PUT", f"/buildings/{bid}/operating-hours", json=periods)

    def downtime(self, bid):
        return self._req("GET", f"/buildings/{bid}/downtime")

    def add_downtime(self, bid, starts_at, ends_at, kind, note=None):
        # Zamanlar bina yerel saati, offset'siz ISO.
        body = {"starts_at": starts_at, "ends_at": ends_at, "kind": kind, **({"note": note} if note else {})}
        return self._req("POST", f"/buildings/{bid}/downtime", json=body)

    def delete_downtime(self, bid, downtime_id):
        return self._req("DELETE", f"/buildings/{bid}/downtime/{downtime_id}")

    def feedback(self, anomaly_id, verdict, note=None):
        body = {"verdict": verdict, **({"note": note} if note else {})}
        return self._req("POST", f"/anomalies/{anomaly_id}/feedback", json=body)
