"""Reshape daily weather columns into rows, validate them, and build dim_date."""

from __future__ import annotations

import calendar
from collections.abc import Iterable
from datetime import date

from config import (
    DECIMAL_PLACES,
    RAINY_DAY_MM,
    TEMP_MAX_ALLOWED,
    TEMP_MIN_ALLOWED,
    season_for,
)

# Internal names for the five measures written to fact_daily_weather.
MEASURES = ("temp_max", "temp_min", "precipitation", "rain", "wind_max")
# A row needs every measure and a date before validation can accept it.
REQUIRED = ("weather_date",) + MEASURES

# Reason codes are stored as text in rejected_records, so keep them stable.
MISSING_VALUE = "missing_required_value"
FUTURE_DATE = "date_after_publication_cutoff"
MAX_BELOW_MIN = "max_below_min"
TEMP_OUT_OF_RANGE = "temperature_out_of_range"
NEGATIVE_PRECIPITATION = "negative_precipitation"
DUPLICATE = "duplicate_town_date"


def _value(series: object, index: int) -> float | None:
    """Read one value at a position in an API column."""
    # A missing or short column becomes a missing value, which validation rejects.
    if not isinstance(series, list) or index >= len(series):
        return None
    raw = series[index]
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def reshape(daily: dict | None, city_name: str) -> list[dict]:
    """Zip the API's column lists into one row per day."""
    if not daily:
        return []

    # The API returns parallel lists, so the position ties a value to a date.
    dates = daily.get("time") or []
    columns = {
        "temp_max": daily.get("temperature_2m_max"),
        "temp_min": daily.get("temperature_2m_min"),
        "precipitation": daily.get("precipitation_sum"),
        "rain": daily.get("rain_sum"),
        "wind_max": daily.get("wind_speed_10m_max"),
    }

    rows = []
    for index, raw_date in enumerate(dates):
        row = {"city": city_name, "weather_date": date.fromisoformat(raw_date)}
        for name, series in columns.items():
            row[name] = _value(series, index)
        rows.append(row)
    return rows


def _rejection_reason(
    row: dict,
    seen: set[tuple[str, date]],
    max_date: date | None,
) -> str | None:
    """Return the first rule that rejects this row."""
    # Return the first failing rule so the reason is deterministic.
    for name in REQUIRED:
        if row.get(name) is None:
            return MISSING_VALUE

    weather_date = row["weather_date"]
    if max_date is not None and weather_date > max_date:
        return FUTURE_DATE

    temp_max = row["temp_max"]
    temp_min = row["temp_min"]
    if temp_max < temp_min:
        return MAX_BELOW_MIN
    if not all(TEMP_MIN_ALLOWED <= value <= TEMP_MAX_ALLOWED for value in (temp_max, temp_min)):
        return TEMP_OUT_OF_RANGE
    if row["precipitation"] < 0 or row["rain"] < 0:
        return NEGATIVE_PRECIPITATION
    if (row["city"], weather_date) in seen:
        return DUPLICATE
    return None


def validate(rows: list[dict], max_date: date | None = None, seen: set[tuple[str, date]] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Return accepted rows and rejected rows with a stable reason code."""
    accepted: list[dict] = []
    rejected: list[dict] = []
    seen = set() if seen is None else seen

    for row in rows:
        reason = _rejection_reason(row, seen, max_date)
        if reason is not None:
            rejected.append(
                {
                    "city": row["city"],
                    "weather_date": row["weather_date"],
                    "reason": reason,
                }
            )
        else:
            accepted.append(row)
            # Only accepted rows enter seen, so the first duplicate wins.
            seen.add((row["city"], row["weather_date"]))
    return accepted, rejected


def derive(row: dict) -> dict:
    """Round measures and add the average temperature and rainy-day flag."""
    # Round first; the average uses the rounded values that reach the fact table.
    result = dict(row)
    for name in MEASURES:
        result[name] = round(result[name], DECIMAL_PLACES)

    temp_max = result["temp_max"]
    temp_min = result["temp_min"]
    result["temp_avg"] = round((temp_max + temp_min) / 2, DECIMAL_PLACES)
    result["is_rainy_day"] = result["precipitation"] >= RAINY_DAY_MM
    return result


def build_dim_date(dates: Iterable[date]) -> list[dict]:
    """Build one calendar row per distinct day."""
    # Monday is 0, so 5 and 6 are Saturday and Sunday.
    return [
        {
            "weather_date": day,
            "year": day.year,
            "month": day.month,
            "month_name": calendar.month_name[day.month],
            "day_of_week": calendar.day_name[day.weekday()],
            "is_weekend": day.weekday() >= 5,
            "season": season_for(day.month),
        }
        for day in sorted(set(dates))
    ]
