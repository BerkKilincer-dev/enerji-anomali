import re
from typing import get_args

import pytest

from src.api.schemas import DowntimeKind
from src.common.db import SCHEMAS
from src.data.customer import TenantStatus
from src.data.load_db import SCHEMA_PATH
from src.data.segments import BDG2_SEGMENTS, SEGMENTS
from src.features.build_features import PRIMARY_USES

SQL = SCHEMA_PATH.read_text()


def test_schema_file_creates_the_known_schemas():
    assert set(re.findall(r"CREATE SCHEMA IF NOT EXISTS (\w+);", SQL)) == set(SCHEMAS)


def test_every_table_and_view_is_schema_qualified():
    names = re.findall(r"CREATE (?:TABLE IF NOT EXISTS|OR REPLACE VIEW) (\S+)", SQL)
    assert names and all(name.split(".")[0] in SCHEMAS for name in names)


@pytest.mark.parametrize(
    ("column", "values"),
    [("segment", SEGMENTS), ("status", get_args(TenantStatus)), ("kind", get_args(DowntimeKind))],
)
def test_check_constraint_matches_the_python_values(column, values):
    checks = re.findall(rf"CHECK \({column} IN \((.*?)\)\)", SQL, re.S)
    assert values in [tuple(re.findall(r"'(\w+)'", check)) for check in checks]


def test_every_bdg2_use_maps_to_a_known_segment():
    assert set(BDG2_SEGMENTS) == set(PRIMARY_USES)
    assert set(BDG2_SEGMENTS.values()) <= set(SEGMENTS)
