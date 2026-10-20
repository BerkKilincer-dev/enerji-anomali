"""Runtime settings, read once from environment variables."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPLITS_PATH = ROOT / "data" / "splits.json"
SEED = 42

ISTANBUL = (41.0082, 28.9784, "Europe/Istanbul")
DEV_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env", override=False)


def _path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    return Path(value).expanduser().resolve() if value else default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    reports_dir: Path
    model_dir: Path
    mlflow_dir: Path
    database_url: str | None
    tariff_tl_per_kwh: float
    default_lat: float
    default_lng: float
    default_timezone: str
    api_key: str | None
    cors_origins: tuple[str, ...]
    mqtt_host: str
    mqtt_port: int
    mqtt_user: str | None
    mqtt_password: str | None
    telegram_bot_token: str | None
    telegram_chat_id: str | None

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_dotenv()
    lat, lng, tz = ISTANBUL
    cors = os.getenv("CORS_ORIGINS", DEV_ORIGINS)
    return Settings(
        data_dir=_path("DATA_DIR", ROOT / "data"),
        reports_dir=_path("REPORTS_DIR", ROOT / "reports"),
        model_dir=_path("MODEL_DIR", ROOT / "models_out"),
        mlflow_dir=_path("MLFLOW_DIR", ROOT / "mlruns"),
        database_url=os.getenv("DATABASE_URL") or None,
        tariff_tl_per_kwh=float(os.getenv("TARIFF_TL_PER_KWH", "3.5")),
        default_lat=float(os.getenv("DEFAULT_LAT", lat)),
        default_lng=float(os.getenv("DEFAULT_LNG", lng)),
        default_timezone=os.getenv("DEFAULT_TIMEZONE", tz),
        api_key=os.getenv("API_KEY") or None,
        cors_origins=tuple(o.strip() for o in cors.split(",") if o.strip()),
        mqtt_host=os.getenv("MQTT_HOST", "localhost"),
        mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
        mqtt_user=os.getenv("MQTT_USER") or None,
        mqtt_password=os.getenv("MQTT_PASSWORD") or None,
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
    )


def require_database_url() -> str:
    url = get_settings().database_url
    if not url:
        sys.exit("DATABASE_URL is not set")
    return url
