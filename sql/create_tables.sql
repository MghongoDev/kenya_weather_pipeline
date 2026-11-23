-- Star schema and control tables for the Kenya weather pipeline.

-- One row per town. city_id comes from the geocoding API.
CREATE TABLE IF NOT EXISTS dim_city (
    city_id    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    county     TEXT,
    latitude   DOUBLE PRECISION NOT NULL,
    longitude  DOUBLE PRECISION NOT NULL,
    elevation  DOUBLE PRECISION,
    population BIGINT
);

-- One row per calendar day in the loaded range.
CREATE TABLE IF NOT EXISTS dim_date (
    weather_date DATE PRIMARY KEY,
    year         INTEGER NOT NULL,
    month        INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    month_name   TEXT NOT NULL,
    day_of_week  TEXT NOT NULL,
    is_weekend   BOOLEAN NOT NULL,
    season       TEXT NOT NULL CHECK (season IN ('Long rains', 'Short rains', 'Dry'))
);

-- One row per town per day. The composite key is the upsert target.
-- The checks mirror the transform rules, so a direct SQL write cannot bypass them.
CREATE TABLE IF NOT EXISTS fact_daily_weather (
    city_id       INTEGER NOT NULL REFERENCES dim_city (city_id),
    weather_date  DATE NOT NULL REFERENCES dim_date (weather_date),
    temp_max      NUMERIC(6, 1) NOT NULL CHECK (temp_max BETWEEN -5 AND 50),
    temp_min      NUMERIC(6, 1) NOT NULL CHECK (temp_min BETWEEN -5 AND 50),
    temp_avg      NUMERIC(6, 1) NOT NULL,
    precipitation NUMERIC(8, 1) NOT NULL CHECK (precipitation >= 0),
    rain          NUMERIC(8, 1) NOT NULL CHECK (rain >= 0),
    wind_max      NUMERIC(8, 1) NOT NULL,
    is_rainy_day  BOOLEAN NOT NULL,
    loaded_at     TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fact_daily_weather_pk PRIMARY KEY (city_id, weather_date),
    CONSTRAINT fact_temp_order CHECK (temp_max >= temp_min)
);

-- One row per town per calendar month. The values come from the daily fact
-- table, so a late revision reaches this table through the same upsert.
CREATE TABLE IF NOT EXISTS monthly_city_weather (
    city_id       INTEGER NOT NULL REFERENCES dim_city (city_id),
    month_start   DATE NOT NULL,
    total_rain_mm NUMERIC(10, 1) NOT NULL CHECK (total_rain_mm >= 0),
    rainy_days    INTEGER NOT NULL CHECK (rainy_days >= 0),
    avg_temp_c    NUMERIC(6, 1) NOT NULL,
    loaded_at     TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT monthly_city_weather_pk PRIMARY KEY (city_id, month_start)
);

-- Control tables: last loaded day per town, bad rows, and one row per run.
CREATE TABLE IF NOT EXISTS etl_watermark (
    city_id          INTEGER PRIMARY KEY REFERENCES dim_city (city_id),
    last_date_loaded DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS rejected_records (
    reject_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id       UUID NOT NULL,
    city         TEXT NOT NULL,
    weather_date DATE,
    reason       TEXT NOT NULL,
    rejected_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS etl_run_log (
    run_id         UUID PRIMARY KEY,
    started_at     TIMESTAMPTZ NOT NULL,
    completed_at   TIMESTAMPTZ,
    status         TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    rows_extracted INTEGER NOT NULL DEFAULT 0,
    rows_loaded    INTEGER NOT NULL DEFAULT 0,
    rows_rejected  INTEGER NOT NULL DEFAULT 0,
    error_message  TEXT
);

CREATE INDEX IF NOT EXISTS idx_fact_weather_date ON fact_daily_weather (weather_date);
CREATE INDEX IF NOT EXISTS idx_rejected_run_id ON rejected_records (run_id);
