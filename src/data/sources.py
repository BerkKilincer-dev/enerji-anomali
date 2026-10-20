"""Public raw data sources with pinned checksums."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    sha256: str
    filename: str


_BDG2 = (
    "https://media.githubusercontent.com/media/buds-lab/building-data-genome-project-2/master/data"
)

LEAD = Source(
    name="lead",
    url="https://raw.githubusercontent.com/samy101/lead-dataset/main/data/lead1.0-small.zip",
    sha256="b0e34c66c0c4ce780b7bb19b199d33e19210675f16be102a5ac32d6b22e39d8f",
    filename="lead1.0-small.zip",
)
BDG2_METADATA = Source(
    name="bdg2_metadata",
    url=f"{_BDG2}/metadata/metadata.csv",
    sha256="992d0b29f24f96ad4332bc4dbb534b7bdd7dd2689aad093f94e93068ecddca02",
    filename="metadata.csv",
)
BDG2_WEATHER = Source(
    name="bdg2_weather",
    url=f"{_BDG2}/weather/weather.csv",
    sha256="a8189f1c6acdf3b9933a9e6354b8e7c1278cd56a7075929623a17565d44f04bd",
    filename="weather.csv",
)

ALL_SOURCES = (LEAD, BDG2_METADATA, BDG2_WEATHER)
