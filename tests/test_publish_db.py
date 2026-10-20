"""Publishing a saved model without training (needs TEST_DATABASE_URL)."""

from __future__ import annotations

from src.data.dataset import model_frame
from src.models import publish, registry
from tests.conftest import make_tables
from tests.test_db import count, db, empty_db, load_customer, pytestmark  # noqa: F401


def test_saved_model_publishes_once(db, scorer, tmp_path, monkeypatch):  # noqa: F811
    tables = make_tables(days=20)
    load_customer(db, tables)
    loads = []

    def fake_tables():
        loads.append(1)
        return tables

    # The synthetic meters are not in splits.json; treat them all as test meters.
    monkeypatch.setattr(publish, "ensure_tables", fake_tables)
    monkeypatch.setattr(publish, "model_frame", lambda t: model_frame(t).assign(split="test"))
    model_dir = registry.save(scorer, tmp_path)

    assert publish.publish_saved(db, model_dir, if_missing=True) == scorer.version
    hours = count(db, "SELECT count(*) FROM predictions WHERE origin = 'batch'")
    assert hours == len(tables.readings)
    assert registry.latest_version(db) == scorer.version
    # A second start finds it published and does not load the data again.
    assert publish.publish_saved(db, model_dir, if_missing=True) == scorer.version
    assert len(loads) == 1
    assert count(db, "SELECT count(*) FROM predictions WHERE origin = 'batch'") == hours
