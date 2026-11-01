import csv

from src.api.recommendations import ANY, MODEL, RECOMMENDATIONS_PATH, recommend, table
from src.data.catalog import EQUIPMENT_TYPES
from src.models.events import DATA_ISSUE_RULES
from src.models.rules import WASTE_RULES

WASTE_TYPES = (*WASTE_RULES, "spike", MODEL)


def test_every_equipment_has_its_own_text_for_every_waste_type():
    texts = table()
    missing = [(e, k) for e in EQUIPMENT_TYPES for k in WASTE_TYPES if (e, k) not in texts]
    assert missing == []
    for kind in (*WASTE_TYPES, *DATA_ISSUE_RULES):
        assert (ANY, kind) in texts
    assert (ANY, ANY) in texts


def test_rows_are_complete_and_unique():
    with RECOMMENDATIONS_PATH.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == len(table())
    assert all(r["title"].strip() and r["action"].strip() for r in rows)
    assert {r["equipment_type"] for r in rows} <= {*EQUIPMENT_TYPES, ANY}


def test_lookup_falls_back_from_equipment_to_any():
    lamp = recommend("aydinlatma", "night_base_load")
    assert "aydınlatma" in lamp["title"].lower()
    assert recommend(None, "night_base_load") == recommend("bilinmeyen", "night_base_load")
    assert recommend("klima", None) == recommend("klima", MODEL)
    stuck = recommend("klima", "flatline")
    assert "veri sorunu" in stuck["action"]
    assert recommend("klima", "new_rule") == {
        "title": table()[(ANY, ANY)]["title"],
        "action": table()[(ANY, ANY)]["action"],
    }
