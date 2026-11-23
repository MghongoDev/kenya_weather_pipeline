"""Town list, API settings, Aiven connection settings, and logging level."""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
LOG_DIR = PROJECT_ROOT / "logs"
SQL_DIR = PROJECT_ROOT / "sql"

# A value set in the shell takes priority over the value in .env.
load_dotenv(PROJECT_ROOT / ".env")

# Default towns for a run. The loader also reads towns saved in dim_city, so
# towns added with --add-city join later runs without a config change.
TOWNS = ("Nairobi", "Mombasa", "Kisumu", "Nakuru", "Eldoret")

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
COUNTRY_CODE = "KE"
TIMEZONE = "Africa/Nairobi"
DAILY_VARIABLES = (
    "temperature_2m_max,temperature_2m_min,"
    "precipitation_sum,rain_sum,wind_speed_10m_max"
)

# The brief fixes the start of the backfill at 1 January 2025.
BACKFILL_START = date(2025, 1, 1)
# Open-Meteo publishes archive data about five days late.
PUBLICATION_DELAY_DAYS = 5
# Open-Meteo can revise recent days. Reread this many published days each run.
LATE_DATA_RELOAD_DAYS = 3
REQUEST_TIMEOUT_SECONDS = 30
RETRY_DELAYS_SECONDS = (1, 2, 4)

TEMP_MIN_ALLOWED = -5.0
TEMP_MAX_ALLOWED = 50.0
RAINY_DAY_MM = 1.0
DECIMAL_PLACES = 1

# Kenya has two rainy seasons. The other months are dry.
LONG_RAINS = "Long rains"
SHORT_RAINS = "Short rains"
DRY = "Dry"


def _setting(name: str, default: str = "") -> str:
    """Return one setting with surrounding spaces removed."""
    return os.environ.get(name, default).strip()


def database_params() -> dict:
    """Build psycopg2 parameters from the Aiven settings in .env."""
    params = {
        "host": _setting("DB_HOST"),
        "port": int(_setting("DB_PORT", "5432")),
        "dbname": _setting("DB_NAME"),
        "user": _setting("DB_USER"),
        "password": _setting("DB_PASSWORD"),
        # Aiven requires TLS. The .env example explains why not to disable it.
        "sslmode": _setting("DB_SSLMODE", "require"),
        # Fail fast when Aiven is unreachable instead of waiting for a long timeout.
        "connect_timeout": 10,
    }
    required = ("host", "dbname", "user", "password")
    missing = [name for name in required if not params[name]]
    if missing:
        raise RuntimeError("Missing .env settings: " + ", ".join(missing))
    return params


def log_level() -> str:
    """Return the logging level named in .env."""
    return _setting("LOG_LEVEL", "INFO").upper()


def publication_cutoff(today: date | None = None) -> date:
    """Return the last day Open-Meteo is expected to have published."""
    if today is None:
        today = date.today()
    return today - timedelta(days=PUBLICATION_DELAY_DAYS)


def season_for(month: int) -> str:
    """Map a month number to a Kenyan rainfall season."""
    if 3 <= month <= 5:
        return LONG_RAINS
    if 10 <= month <= 12:
        return SHORT_RAINS
    return DRY
