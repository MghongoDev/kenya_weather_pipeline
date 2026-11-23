"""Create the tables and write each stage with an idempotent upsert."""

from __future__ import annotations

from datetime import date, datetime

from psycopg2.extras import execute_values

from config import SQL_DIR

# Each writer uses the table key as its conflict target. A rerun then replaces
# the row with that key instead of adding a second copy.
UPSERT_CITY = """
INSERT INTO dim_city (city_id, name, county, latitude, longitude, elevation, population)
VALUES %s
ON CONFLICT (city_id) DO UPDATE SET
    name = EXCLUDED.name,
    county = EXCLUDED.county,
    latitude = EXCLUDED.latitude,
    longitude = EXCLUDED.longitude,
    elevation = EXCLUDED.elevation,
    population = EXCLUDED.population
"""

UPSERT_DATE = """
INSERT INTO dim_date (weather_date, year, month, month_name, day_of_week, is_weekend, season)
VALUES %s
ON CONFLICT (weather_date) DO UPDATE SET
    year = EXCLUDED.year,
    month = EXCLUDED.month,
    month_name = EXCLUDED.month_name,
    day_of_week = EXCLUDED.day_of_week,
    is_weekend = EXCLUDED.is_weekend,
    season = EXCLUDED.season
"""

# The fact key is the town and date pair. Either column alone repeats.
UPSERT_FACT = """
INSERT INTO fact_daily_weather (
    city_id, weather_date, temp_max, temp_min, temp_avg,
    precipitation, rain, wind_max, is_rainy_day
) VALUES %s
ON CONFLICT (city_id, weather_date) DO UPDATE SET
    temp_max = EXCLUDED.temp_max,
    temp_min = EXCLUDED.temp_min,
    temp_avg = EXCLUDED.temp_avg,
    precipitation = EXCLUDED.precipitation,
    rain = EXCLUDED.rain,
    wind_max = EXCLUDED.wind_max,
    is_rainy_day = EXCLUDED.is_rainy_day,
    loaded_at = CURRENT_TIMESTAMP
"""

# The monthly fact is derived, so rebuild it from the daily fact after the
# fact load. An upsert on town and month keeps late revisions idempotent.
UPSERT_MONTHLY = """
INSERT INTO monthly_city_weather (
    city_id, month_start, total_rain_mm, rainy_days, avg_temp_c
)
SELECT
    city_id,
    DATE_TRUNC('month', weather_date)::date,
    ROUND(SUM(precipitation), 1),
    COUNT(*) FILTER (WHERE is_rainy_day),
    ROUND(AVG(temp_avg), 1)
FROM fact_daily_weather
GROUP BY city_id, DATE_TRUNC('month', weather_date)
ON CONFLICT (city_id, month_start) DO UPDATE SET
    total_rain_mm = EXCLUDED.total_rain_mm,
    rainy_days = EXCLUDED.rainy_days,
    avg_temp_c = EXCLUDED.avg_temp_c,
    loaded_at = CURRENT_TIMESTAMP
"""

# Rejects are appended, not upserted. Each run keeps its own rows for audit.
INSERT_REJECT = """
INSERT INTO rejected_records (run_id, city, weather_date, reason)
VALUES %s
"""

# GREATEST stops an older batch from moving the watermark backwards.
UPSERT_WATERMARK = """
INSERT INTO etl_watermark (city_id, last_date_loaded)
VALUES (%s, %s)
ON CONFLICT (city_id) DO UPDATE SET
    last_date_loaded = GREATEST(etl_watermark.last_date_loaded, EXCLUDED.last_date_loaded)
"""

# One row per run ID. The failure path reuses the row written at start.
RECORD_RUN = """
INSERT INTO etl_run_log (
    run_id, started_at, completed_at, status,
    rows_extracted, rows_loaded, rows_rejected, error_message
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (run_id) DO UPDATE SET
    completed_at = EXCLUDED.completed_at,
    status = EXCLUDED.status,
    rows_extracted = EXCLUDED.rows_extracted,
    rows_loaded = EXCLUDED.rows_loaded,
    rows_rejected = EXCLUDED.rows_rejected,
    error_message = EXCLUDED.error_message
"""


def create_tables(conn) -> None:
    """Create all tables from the committed SQL definition."""
    # This commits before the data transaction so a failed run can still log.
    script = (SQL_DIR / "create_tables.sql").read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(script)


def existing_city_names(conn) -> list[str]:
    """Return towns already present in dim_city, including added ones."""
    # Towns added with --add-city persist here, so later runs include them.
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM dim_city ORDER BY name")
        return [row[0] for row in cur.fetchall()]


def get_watermark(conn, city_id: int) -> date | None:
    """Return the last loaded day for a town."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT last_date_loaded FROM etl_watermark WHERE city_id = %s",
            (city_id,),
        )
        row = cur.fetchone()
        return row[0] if row else None


# The writers skip empty batches. execute_values sends rows in pages, which keeps
# the round trips to Aiven low enough for a full backfill.
def upsert_cities(conn, cities: list[dict]) -> None:
    """Insert or update one row per town."""
    if not cities:
        return
    values = [
        (
            city["city_id"], city["name"], city["county"], city["latitude"],
            city["longitude"], city["elevation"], city["population"],
        )
        for city in cities
    ]
    with conn.cursor() as cur:
        execute_values(cur, UPSERT_CITY, values)


def upsert_dates(conn, days: list[dict]) -> None:
    """Insert or update one row per calendar day."""
    if not days:
        return
    values = [
        (
            day["weather_date"], day["year"], day["month"], day["month_name"],
            day["day_of_week"], day["is_weekend"], day["season"],
        )
        for day in days
    ]
    with conn.cursor() as cur:
        execute_values(cur, UPSERT_DATE, values)


def upsert_facts(conn, rows: list[dict]) -> None:
    """Insert or update one fact row per town per day."""
    if not rows:
        return
    values = [
        (
            row["city_id"], row["weather_date"], row["temp_max"], row["temp_min"],
            row["temp_avg"], row["precipitation"], row["rain"], row["wind_max"],
            row["is_rainy_day"],
        )
        for row in rows
    ]
    with conn.cursor() as cur:
        execute_values(cur, UPSERT_FACT, values)


# A late-window reread changes past days, so rebuild the monthly rows from all
# current facts instead of adding the new days to the existing totals.
def upsert_monthly_summary(conn) -> None:
    """Rebuild monthly aggregates from the fact table inside the run transaction."""
    with conn.cursor() as cur:
        cur.execute(UPSERT_MONTHLY)


def insert_rejects(conn, run_id: str, rows: list[dict]) -> None:
    """Write bad records with the reason code from the transform."""
    if not rows:
        return
    values = [(run_id, row["city"], row["weather_date"], row["reason"]) for row in rows]
    with conn.cursor() as cur:
        execute_values(cur, INSERT_REJECT, values)


def upsert_watermark(conn, city_id: int, last_date: date) -> None:
    """Move a town's watermark forward without moving it backwards."""
    with conn.cursor() as cur:
        cur.execute(UPSERT_WATERMARK, (city_id, last_date))


def record_run(
    conn,
    run_id: str,
    started_at: datetime,
    status: str,
    rows_extracted: int = 0,
    rows_loaded: int = 0,
    rows_rejected: int = 0,
    error_message: str | None = None,
    completed_at: datetime | None = None,
) -> None:
    """Insert or update one row in the run log."""
    with conn.cursor() as cur:
        cur.execute(
            RECORD_RUN,
            (
                run_id, started_at, completed_at, status,
                rows_extracted, rows_loaded, rows_rejected, error_message,
            ),
        )
