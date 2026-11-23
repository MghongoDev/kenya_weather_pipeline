"""Fetch JSON from Open-Meteo with retries and map it to town and weather fields."""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

import requests

from config import (
    ARCHIVE_URL,
    COUNTRY_CODE,
    DAILY_VARIABLES,
    GEOCODING_URL,
    RETRY_DELAYS_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    TIMEZONE,
)

log = logging.getLogger(__name__)


class ExtractError(RuntimeError):
    """Raised when a request or its response cannot be used."""


def get_json(url: str, params: dict | None = None, timeout: int = REQUEST_TIMEOUT_SECONDS) -> dict:
    """Fetch JSON with exponential backoff after transient failures."""
    # Three delays give four attempts: the first call plus three retries.
    delays = RETRY_DELAYS_SECONDS
    attempts = len(delays) + 1
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, params=params, timeout=timeout)
            status = response.status_code
            # Retry rate limits and server errors. A bad request will not fix itself.
            if status == 429 or status >= 500:
                raise requests.HTTPError(f"HTTP {status}", response=response)
            response.raise_for_status()
            return response.json()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status is not None and 400 <= status < 500 and status != 429:
                raise ExtractError(f"GET {url} returned HTTP {status}") from exc
            last_error = exc
        except (requests.RequestException, ValueError) as exc:
            # ValueError covers a body that is not valid JSON.
            last_error = exc

        if attempt < attempts:
            time.sleep(delays[attempt - 1])

    raise ExtractError(f"GET {url} failed after {attempts} attempts: {last_error}") from last_error


def _as_float(value: Any) -> float | None:
    """Convert one API value, keeping a missing value as None."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    """Convert one whole-number API value, keeping a missing value as None."""
    parsed = _as_float(value)
    # Parsing through float also accepts a numeric string written as "45000.0".
    return int(parsed) if parsed is not None else None


def geocode(town: str) -> dict:
    """Look up one Kenyan town and return its dimension attributes."""
    # Filter in the request, then check the response as well.
    data = get_json(
        GEOCODING_URL,
        {"name": town, "countryCode": COUNTRY_CODE, "count": 1},
    )
    results = data.get("results") or []
    if not results:
        raise ExtractError(f"geocoding found no Kenyan match for {town}")

    hit = results[0]
    if hit.get("country_code") != COUNTRY_CODE:
        raise ExtractError(f"geocoding match for {town} is outside Kenya")
    if hit.get("latitude") is None or hit.get("longitude") is None:
        raise ExtractError(f"geocoding match for {town} has no coordinates")

    return {
        "city_id": int(hit["id"]),
        "name": hit.get("name") or town,
        "county": hit.get("admin1"),
        "latitude": float(hit["latitude"]),
        "longitude": float(hit["longitude"]),
        # Open-Meteo can omit elevation and population, so both stay nullable.
        "elevation": _as_float(hit.get("elevation")),
        "population": _as_int(hit.get("population")),
    }


def fetch_weather(city: dict, start: date, end: date) -> dict | None:
    """Fetch the daily block for one town over an inclusive date range."""
    # A town with no new days makes no request.
    if start > end:
        log.info("%s: no days to request (start %s, cutoff %s)", city["name"], start, end)
        return None

    data = get_json(
        ARCHIVE_URL,
        {
            "latitude": city["latitude"],
            "longitude": city["longitude"],
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": DAILY_VARIABLES,
            "timezone": TIMEZONE,
        },
    )
    # A missing daily block means the response shape changed.
    daily = data.get("daily")
    if not isinstance(daily, dict):
        raise ExtractError(f"weather response for {city['name']} has no daily block")
    return daily
