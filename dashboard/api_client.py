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

    def readings(self, bid, hours):
        return self._req("GET", f"/buildings/{bid}/readings", params={"hours": hours})

    def forecast(self, bid, hours):
        return self._req("GET", f"/buildings/{bid}/forecast", params={"hours": hours})

    def anomalies(self, bid, limit=50):
        return self._req("GET", "/anomalies", params={"building_id": bid, "limit": limit})