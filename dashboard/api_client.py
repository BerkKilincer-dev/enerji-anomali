import json
import logging
import os
import threading
import time
from collections import deque
from datetime import datetime

import requests

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
CACHE_TTL = 30  # sn; aynı GET bu süre içinde tekrar istenirse API'ye gitmez
SLOW_MS = 1000  # bundan uzun süren istek günlüğe uyarı olarak düşer

log = logging.getLogger("dashboard.api")
if not log.handlers:  # Streamlit modülü yeniden yüklediğinde handler çoğalmasın
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False

# Süreç genelinde son hatalar ve yavaş istekler; Ayarlar'daki sistem günlüğü kartı okur (yalnız admin).
EVENTS = deque(maxlen=100)
_events_lock = threading.Lock()


def _event(level, user, method, path, status, ms, message):
    with _events_lock:
        EVENTS.append(
            {"zaman": datetime.now(), "seviye": level, "kullanıcı": user or "-", "istek": f"{method} {path}",
             "durum": status, "süre_ms": ms, "mesaj": message}
        )


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Api:
    """Oturum cookie'sini requests.Session ile saklar.

    Her kullanıcının kendi nesnesi olduğu için GET önbelleği kullanıcılar arasında paylaşılmaz.
    """

    def __init__(self):
        self.s = requests.Session()
        self.user = None
        self._cache = {}
        self.reset_stats()

    def reset_stats(self):
        # Sayfa başına sayaç: app.py her çalıştırmanın başında sıfırlar, durum çubuğu okur.
        self.stats = {"calls": 0, "cached": 0, "ms": 0}

    def _state(self):
        # Sınıf yeniden yüklenip eski nesneye bağlanınca yeni alanlar eksik olabilir.
        for k, v in (("user", None), ("_cache", {}), ("stats", None)):
            if k not in self.__dict__:
                setattr(self, k, v)
        if self.stats is None:
            self.reset_stats()

    def _req(self, method, path, **kw):
        self._state()
        key = (path, json.dumps(kw.get("params"), sort_keys=True, default=str)) if method == "GET" else None
        hit = self._cache.get(key) if key else None
        if hit and time.monotonic() - hit[0] < CACHE_TTL:
            self.stats["cached"] += 1
            return json.loads(hit[1])  # her seferinde yeni kopya: çağıran değiştirse önbellek bozulmaz
        if method != "GET":
            self._cache.clear()  # yazma sonrası ekranda eski veri kalmasın

        headers = {"X-Requested-With": "dashboard"} if method != "GET" else {}
        t0 = time.perf_counter()
        try:
            r = self.s.request(method, f"{API_URL}{path}", headers=headers, timeout=15, **kw)
        except requests.RequestException as exc:
            ms = round((time.perf_counter() - t0) * 1000)
            log.error("%s %s ulaşılamadı (%d ms): %s", method, path, ms, exc)
            _event("HATA", self.user, method, path, 0, ms, f"API'ye ulaşılamadı: {type(exc).__name__}")
            raise ApiError(0, "API'ye ulaşılamadı, bağlantıyı kontrol edin.") from exc
        ms = round((time.perf_counter() - t0) * 1000)
        with _events_lock:  # paralel okumalarda sayaçlar kaybolmasın
            self.stats["calls"] += 1
            self.stats["ms"] += ms

        if not r.ok:
            try:
                detail = r.json().get("detail", r.reason)
                if isinstance(detail, list):  # 422 doğrulama hataları liste döner
                    detail = "; ".join(d.get("msg", str(d)) if isinstance(d, dict) else str(d) for d in detail)
            except Exception:
                detail = r.reason
            # 401 oturum bitişi beklenen bir durum; hata olarak sayılmaz.
            seviye = "UYARI" if r.status_code in (401, 404) else "HATA"
            log.log(logging.WARNING if seviye == "UYARI" else logging.ERROR, "%s %s -> %d (%d ms): %s",
                    method, path, r.status_code, ms, detail)
            _event(seviye, self.user, method, path, r.status_code, ms, str(detail))
            raise ApiError(r.status_code, str(detail))

        if ms >= SLOW_MS:
            log.warning("%s %s yavaş: %d ms", method, path, ms)
            _event("YAVAŞ", self.user, method, path, r.status_code, ms, "yavaş yanıt")
        if r.status_code == 204:
            return None
        if key:
            self._cache[key] = (time.monotonic(), r.content)
        return r.json()

    def login(self, email, password):
        out = self._req("POST", "/auth/login", json={"email": email, "password": password})
        self.user = email
        return out

    def logout(self):
        try:
            return self._req("POST", "/auth/logout")
        finally:
            self._cache.clear()
            self.user = None

    def health(self):
        return self._req("GET", "/health")

    def summary(self, customers_only=False):
        return self._req("GET", "/summary", params={"customers_only": customers_only} if customers_only else None)

    def buildings(self):
        return self._req("GET", "/buildings")

    def readings(self, bid, hours, end=None):
        # end: pencerenin son saati (bina yerel saati, ISO); yoksa son okumada biter.
        params = {"hours": hours, **({"end": end} if end else {})}
        return self._req("GET", f"/buildings/{bid}/readings", params=params)

    def forecast(self, bid, hours, end=None):
        params = {"hours": hours, **({"end": end} if end else {})}
        return self._req("GET", f"/buildings/{bid}/forecast", params=params)

    def anomalies(self, bid=None, limit=50, **filters):
        # filters: tenant_id, category, status, equipment_type, since, until, min_cost_tl, order
        params = {"limit": limit, **({"building_id": bid} if bid is not None else {})}
        params.update({k: v for k, v in filters.items() if v is not None})
        return self._req("GET", "/anomalies", params=params)

    def tenant_summaries(self, days=30):
        return self._req("GET", "/tenants/summary", params={"days": days})

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
