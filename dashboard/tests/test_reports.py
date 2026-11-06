"""reports.collect: rapor tablosu ve dışa aktarmalar API'nin verdiği veriden doğru üretilir."""

from datetime import datetime, timedelta

import pytest

from reports import collect

END = datetime(2016, 12, 31, 23)


class FakeApi:
    """İki gün okuma ve her durumda birer alarm döndüren sahte API."""

    VERDICTS = [None, "sent", "applied", "confirmed", "false_alarm", "planned"]

    def readings(self, bid, hours, end=None):
        return [
            {"building_id": bid, "ts": (END - timedelta(hours=h)).isoformat(), "kwh": 10.0, "anomaly": 0}
            for h in range(hours)
        ]

    def anomalies(self, bid, limit=50, **filters):
        return [
            {
                "id": i + 1,
                "ts_start": (END - timedelta(hours=i + 1)).isoformat(),
                "ts_end": (END - timedelta(hours=i)).isoformat(),
                "category": "waste",
                "excess_kwh": 2.0,
                "est_cost_tl": 11.86,
                "verdict": v,
                "explanation": "test",
            }
            for i, v in enumerate(self.VERDICTS)
        ]


@pytest.fixture
def table():
    return collect(FakeApi(), {1: "Sayaç 1"}, 24, {1: 5.93})["anomaliler"]


def test_every_verdict_has_its_own_label(table):
    assert sorted(table["Durum"]) == sorted(
        ["Bekliyor", "İletildi", "Uygulandı", "Onaylandı", "Yanlış alarm", "Planlı"]
    )
