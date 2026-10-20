"""Fixed building segments; the database CHECK in core.buildings uses the same list."""

from __future__ import annotations

from typing import Literal, get_args

Segment = Literal[
    "office",
    "mall",
    "retail",
    "hotel",
    "hospital",
    "education",
    "factory",
    "warehouse",
    "data_center",
    "residential",
    "public",
    "other",
]
SEGMENTS: tuple[str, ...] = get_args(Segment)

BDG2_SEGMENTS = {
    "Education": "education",
    "Entertainment/public assembly": "public",
    "Food sales and service": "retail",
    "Healthcare": "hospital",
    "Lodging/residential": "residential",
    "Manufacturing/industrial": "factory",
    "Office": "office",
    "Other": "other",
    "Parking": "other",
    "Public services": "public",
    "Religious worship": "public",
    "Retail": "retail",
    "Services": "other",
    "Technology/science": "other",
    "Utility": "other",
    "Warehouse/storage": "warehouse",
}
