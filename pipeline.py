"""Run extract, transform, and load in one transaction, then record the run."""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import psycopg2

import config
import load
from extract import ExtractError, fetch_weather, geocode
from transform import build_dim_date, derive, reshape, validate


def setup_logging() -> logging.Logger:
    """Send pipeline messages to logs/pipeline.log and the console."""
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("weather")
    # An unknown level in .env falls back to INFO instead of stopping the run.
    level = getattr(logging, config.log_level(), None)
    logger.setLevel(level if isinstance(level, int) else logging.INFO)
    if logger.handlers:
        return logger

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(config.LOG_DIR / "pipeline.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def run(
    towns: list[str] | None = None,
    full_refresh: bool = False,
    log: logging.Logger | None = None,
    connection_params: dict | None = None,
) -> int:
    """Run one full pipeline cycle. Return 0 on success and 1 on failure."""
    log = log or logging.getLogger("weather")
    run_id = str(uuid4())
    started_at = datetime.now(timezone.utc)
    counts = {"extracted": 0, "loaded": 0, "rejected": 0}
    # A caller may pass parameters for a test database. The CLI uses Aiven from .env.
    params = connection_params if connection_params is not None else config.database_params()
    conn = psycopg2.connect(**params)

    try:
        # The schema commits first, so a failed data load can still write a run log.
        with conn:
            load.create_tables(conn)
        with conn:
            load.record_run(conn, run_id, started_at, "running")

            known = load.existing_city_names(conn)
            # Config towns, CLI additions, then towns already in dim_city.
            parts: list[str] = list(config.TOWNS)
            parts.extend(towns or [])
            parts.extend(known)
            todo = list(dict.fromkeys(parts))
            cutoff = config.publication_cutoff()

            cities: list[dict] = []
            days: set[date] = set()
            facts: list[dict] = []
            rejects: list[dict] = []
            watermarks: dict[int, date] = {}
            failures: dict[str, str] = {}

            for town in todo:
                try:
                    city = geocode(town)
                except ExtractError as exc:
                    log.error("%s: geocoding failed: %s", town, exc)
                    failures[town] = str(exc)
                    continue

                cities.append(city)
                city_id = city["city_id"]
                # No watermark means first run or full refresh, so use the backfill date.
                watermark = None if full_refresh else load.get_watermark(conn, city_id)
                base = watermark + timedelta(days=1) if watermark else config.BACKFILL_START
                # Recent days can change after publication. Reread the late
                # window even when the watermark already reaches the cutoff.
                late_start = cutoff - timedelta(days=config.LATE_DATA_RELOAD_DAYS - 1)
                start = min(base, late_start)
                if start > cutoff:
                    log.info("%s: nothing to do (start %s, cutoff %s)", town, start, cutoff)
                    continue

                try:
                    daily = fetch_weather(city, start, cutoff)
                except ExtractError as exc:
                    log.error("%s: weather fetch failed: %s", town, exc)
                    failures[town] = str(exc)
                    continue

                raw = reshape(daily, city["name"])
                accepted, rejected = validate(raw, max_date=cutoff)
                derived = [derive(row) for row in accepted]
                for row in derived:
                    row["city_id"] = city_id

                # Check the counts before writing anything for this town.
                if len(raw) != len(derived) + len(rejected):
                    raise RuntimeError(f"{town}: extracted count does not reconcile")

                log.info(
                    "%s: %s to %s, extracted %s, loaded %s, rejected %s",
                    town, start, cutoff, len(raw), len(derived), len(rejected),
                )
                counts["extracted"] += len(raw)
                counts["loaded"] += len(derived)
                counts["rejected"] += len(rejected)
                rejects.extend(rejected)
                days.update(row["weather_date"] for row in derived)
                facts.extend(derived)
                if derived:
                    newest = max(row["weather_date"] for row in derived)
                    previous = watermarks.get(city_id)
                    watermarks[city_id] = newest if previous is None else max(newest, previous)

            # One failed town is isolated; no data from any town means a failed run.
            if todo and len(failures) == len(todo):
                detail = ", ".join(f"{town} ({reason})" for town, reason in failures.items())
                raise RuntimeError(f"all towns failed extraction: {detail}")

            # Dimensions before facts so the foreign keys resolve.
            load.upsert_cities(conn, cities)
            load.upsert_dates(conn, build_dim_date(days))
            load.upsert_facts(conn, facts)
            # The monthly fact depends on the daily fact, so rebuild it here.
            load.upsert_monthly_summary(conn)
            load.insert_rejects(conn, run_id, rejects)
            for city_id, last_date in watermarks.items():
                load.upsert_watermark(conn, city_id, last_date)
            load.record_run(
                conn, run_id, started_at, "success",
                counts["extracted"], counts["loaded"], counts["rejected"],
                None, datetime.now(timezone.utc),
            )

        log.info("run %s committed: %s", run_id, counts)
        return 0

    except Exception as exc:
        conn.rollback()
        log.exception("run %s failed: %s", run_id, exc)
        # Write the failure in a new transaction after the rollback.
        try:
            with conn:
                load.record_run(
                    conn, run_id, started_at, "failed",
                    counts["extracted"], counts["loaded"], counts["rejected"],
                    str(exc), datetime.now(timezone.utc),
                )
        except Exception:
            log.exception("run %s: could not write the failed run log", run_id)
        return 1
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    """Parse command line options and run the pipeline."""
    parser = argparse.ArgumentParser(description="Load daily weather for Kenyan towns")
    parser.add_argument(
        "--add-city",
        metavar="NAME",
        help="geocode and backfill one extra town",
    )
    parser.add_argument(
        "--full-refresh",
        action="store_true",
        help="ignore watermarks and reload from the backfill start date",
    )
    args = parser.parse_args(argv)
    extra = [args.add_city] if args.add_city else []
    return run(towns=extra, full_refresh=args.full_refresh, log=setup_logging())


if __name__ == "__main__":
    raise SystemExit(main())
