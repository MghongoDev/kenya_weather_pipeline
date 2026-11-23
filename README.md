# Kenya City Weather Pipeline

This pipeline loads daily weather history for five Kenyan towns into a PostgreSQL star schema. It fetches from Open-Meteo, validates every day, and writes dimensions and facts in one transaction. Each run requests only days that Open-Meteo has published.

The advisory team uses the tables to answer three questions. Which town had the most rainfall each month? How many rainy days did each town have in each season? Which days were the hottest and coldest?

## Project status

The pipeline has completed full backfills for the five default towns. `logs/pipeline.log` holds the run history. A second run on the same day rereads only the three-day late window and adds no duplicate rows. The delete-and-reload test below checks that watermarks select the right dates.

The acceptance checks below are the verification path.

## Cost

Open-Meteo is free for non-commercial use and needs no API key. One run makes one geocoding call and one weather call per town. The Aiven plan sets the database cost. This project does not track it.

## Quick start

You need Python 3.10 or newer, a PostgreSQL database, and internet access.

1. Install the packages.

   ```
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in the Aiven values. Keep `DB_SSLMODE=require`. Aiven needs TLS.

   ```
   DB_HOST=your-aiven-host.aivencloud.com
   DB_PORT=12345
   DB_NAME=defaultdb
   DB_USER=avnadmin
   DB_PASSWORD=your-password
   DB_SSLMODE=require
   LOG_LEVEL=INFO
   ```

3. Run the pipeline.

   ```
   python pipeline.py
   ```

The first run loads every day from 1 January 2025 through five days before today. A second run on the same day loads nothing and duplicates nothing. 

## Layout

```
kenya_weather_pipeline/
├── .env
├── .gitignore
├── requirements.txt
├── config.py
├── extract.py
├── transform.py
├── load.py
├── pipeline.py
├── sql/
│   ├── create_tables.sql
│   └── business_questions.sql
├── logs/
└── README.md
```

A `.env.example` template sits beside `.env` for setup. It holds no real password.

`config.py` holds the town list, endpoints, dates, Aiven settings, and logging level. `extract.py` fetches JSON and maps the response fields. `transform.py` reshapes columns into rows, validates them, and builds the calendar dimension. `load.py` creates tables and writes rows with upserts. `pipeline.py` runs the stages and records the outcome. The `logs` folder holds `pipeline.log` after the first run.

This README holds the prose documentation. The Python and SQL files explain their own decisions in comments. The layout has no separate tutorial, how-to, reference, or explanation directories.

## How the pipeline works

For each town, the pipeline looks up its Open-Meteo id, latitude, longitude, county, elevation, and population. It reads the watermark, which is the last day loaded for that town. It requests weather from the earlier of the day after the watermark or the start of the late window, through five days before today. On the first run it starts at 1 January 2025.

Open-Meteo returns parallel lists, one list per measure. The transform zips those lists into one row per town per day. It rounds each measure to one decimal place. It computes the average temperature from the maximum and minimum. It sets `is_rainy_day` when precipitation reaches 1 mm.

Rows that fail validation go to `rejected_records` with a reason code. Valid rows go to the fact table. The load writes `dim_city` first, then `dim_date`, then `fact_daily_weather`, then the watermark. All writes share one transaction. The watermark advances only when that transaction commits.

Open-Meteo can revise recent published days. Each run rereads the last three published days. The upsert on town and date replaces those rows, so the reread adds no copies. The run then rebuilds `monthly_city_weather` from the daily fact table in the same transaction.

If one town fails after its retries, the pipeline logs the error and continues with the other towns. If the database write fails, the transaction rolls back and the run log records a failed run.

## Tables

| Table | Grain | Key columns |
|---|---|---|
| `dim_city` | one town | `city_id`, `name`, `county`, coordinates, elevation, population |
| `dim_date` | one calendar day | `weather_date`, year, month, `month_name`, `day_of_week`, `is_weekend`, `season` |
| `fact_daily_weather` | one town on one day | composite key `city_id`, `weather_date`; temperatures, precipitation, rain, wind, `is_rainy_day`, `loaded_at` |
| `monthly_city_weather` | one town in one month | composite key `city_id`, `month_start`; total rainfall, rainy days, average temperature |
| `etl_watermark` | one town | `city_id`, `last_date_loaded` |
| `rejected_records` | one bad record | `run_id`, city, date, reason, `rejected_at` |
| `etl_run_log` | one pipeline run | `run_id`, times, row counts, status, error message |

`season` takes three values. March through May is `Long rains`. October through December is `Short rains`. The other months are `Dry`.

The fact table uses a composite key because one town has many days and one date has many towns. The upsert targets that pair, so a rerun updates existing rows instead of adding copies.

## Terms

| Term | Meaning |
|---|---|
| Backfill | The first load, from `BACKFILL_START` through the cutoff |
| Cutoff | The last day Open-Meteo is expected to have published |
| Late window | The last three published days. Every run rereads them to pick up revisions |
| Grain | What one row in a table represents |
| Reject | A day that fails validation and goes to `rejected_records` with a reason code |
| Upsert | An insert that updates the row with the same key instead of adding a copy |
| Watermark | The last day loaded for one town. The next run starts the day after it |

## Configuration

Settings live at the top of `config.py`.

| Setting | Value | Purpose |
|---|---|---|
| `TOWNS` | Nairobi, Mombasa, Kisumu, Nakuru, Eldoret | towns loaded on every run |
| `GEOCODING_URL`, `ARCHIVE_URL` | Open-Meteo endpoints | source systems |
| `BACKFILL_START` | `2025-01-01` | first day of the backfill |
| `PUBLICATION_DELAY_DAYS` | `5` | keeps requests inside published data |
| `LATE_DATA_RELOAD_DAYS` | `3` | recent published days reread on every run |
| `REQUEST_TIMEOUT_SECONDS` | `30` | HTTP timeout |
| `RETRY_DELAYS_SECONDS` | `1, 2, 4` | exponential backoff |
| `TEMP_MIN_ALLOWED`, `TEMP_MAX_ALLOWED` | `-5.0`, `50.0` | temperature bounds |
| `RAINY_DAY_MM` | `1.0` | rainy-day threshold |
| `DAILY_VARIABLES` | max and min temperature, precipitation, rain, max wind | requested daily fields |
| `TIMEZONE` | `Africa/Nairobi` | response timezone |
| `DB_HOST`, `DB_PORT` | Aiven host and port | database endpoint |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD` | Aiven database and login | database credentials |
| `DB_SSLMODE` | `require` | TLS to Aiven |
| `LOG_LEVEL` | `INFO` | console and file logging level |

The Aiven values come from `.env` through `python-dotenv`. `DB_SSLMODE` stays at `require`. No password appears in code, logs, or version control. A value set in the shell takes priority over `.env`.

## Commands

Run the daily incremental load.

```
python pipeline.py
```

Add a sixth town and backfill it. Later runs include it because the pipeline reads town names from `dim_city`.

```
python pipeline.py --add-city Machakos
```

Reload all published days from the backfill start. Upserts keep row counts stable.

```
python pipeline.py --full-refresh
```

Answer the three business questions. `psql` asks for the password, so it never appears in the command.

```
psql "$(python -c "import config; p=config.database_params(); print('host=%s port=%s dbname=%s user=%s sslmode=%s' % (p['host'], p['port'], p['dbname'], p['user'], p['sslmode']))")" -f sql/business_questions.sql
```

## Validation rules

The transform rejects a row for any of these reasons.

| Reason code | Condition |
|---|---|
| `missing_required_value` | a required measure or the date is null |
| `date_after_publication_cutoff` | the date is newer than five days ago |
| `max_below_min` | maximum temperature is below minimum temperature |
| `temperature_out_of_range` | a temperature falls outside -5 to 50 °C |
| `negative_precipitation` | precipitation or rain is negative |
| `duplicate_town_date` | the same town and date appear twice in one batch |

A rejected row never reaches the fact table. The run log records the count for each run.

## Check a run

Show the last few runs.

```sql
SELECT started_at, completed_at, status, rows_extracted, rows_loaded, rows_rejected, error_message
FROM etl_run_log
ORDER BY started_at DESC
LIMIT 5;
```

Show the watermark for each town.

```sql
SELECT c.name, w.last_date_loaded
FROM etl_watermark AS w
JOIN dim_city AS c USING (city_id)
ORDER BY c.name;
```

Check that extraction equals loading plus rejection for the last run.

```sql
SELECT status, rows_extracted, rows_loaded, rows_rejected
FROM etl_run_log
WHERE status = 'success'
ORDER BY started_at DESC
LIMIT 1;
```

Show rejected rows from the last run.

```sql
SELECT r.city, r.weather_date, r.reason
FROM rejected_records AS r
WHERE r.run_id = (SELECT run_id FROM etl_run_log ORDER BY started_at DESC LIMIT 1)
ORDER BY r.city, r.weather_date;
```

Show the monthly fact for every town.

```sql
SELECT c.name, TO_CHAR(m.month_start, 'YYYY-MM') AS month,
       m.total_rain_mm, m.rainy_days, m.avg_temp_c
FROM monthly_city_weather AS m
JOIN dim_city AS c USING (city_id)
ORDER BY c.name, m.month_start;
```

## Delete-and-reload test

This test checks that watermarks select the right dates.

1. Run the pipeline once so Mombasa has data.
2. Delete the last seven loaded days for Mombasa and move its watermark back to the day before them. Use the watermark, not `CURRENT_DATE`, because Aiven reports UTC and the pipeline uses `Africa/Nairobi`.

   ```sql
   DELETE FROM fact_daily_weather
   WHERE city_id = (SELECT city_id FROM dim_city WHERE name = 'Mombasa')
     AND weather_date > (
       SELECT MAX(last_date_loaded) - INTERVAL '7 days' FROM etl_watermark
     );

   UPDATE etl_watermark
   SET last_date_loaded = (
     SELECT MAX(last_date_loaded) - INTERVAL '7 days' FROM etl_watermark
   )
   WHERE city_id = (SELECT city_id FROM dim_city WHERE name = 'Mombasa');
   ```

3. Run `python pipeline.py` again. The log shows seven rows loaded for Mombasa and zero for the other towns. Row counts return to the earlier total.

The same idea applies to revised data. Reloading a range is safe because the upsert replaces matching rows and leaves the rest alone.

## Scheduling

On Linux or macOS, add a cron entry. Set the paths to match your machine.

```
0 6 * * * cd /path/to/kenya_weather_pipeline && /path/to/venv/bin/python pipeline.py >> logs/pipeline.log 2>&1
```

On Windows, use Task Scheduler with `python pipeline.py` as the action and the pipeline folder as the working directory.

## Failure paths

A geocoding or weather request can fail after three retries. The pipeline logs that town and processes the rest. Those towns keep their old watermarks, so a later run picks them up again. If every town fails, the run is marked failed and the error message names each town.

A database error rolls back the transaction. The fact rows, dimension rows, and watermark change together or not at all. The run log gets a `failed` row with the error message. Block the Open-Meteo host while leaving the database reachable to see this path. Turning off all network access also blocks Aiven, so the pipeline cannot write a run log row.

## Troubleshooting

| Symptom | Check | Likely cause |
|---|---|---|
| A rerun loads zero rows | Watermark query in Check a run | The town is loaded through the cutoff |
| A town is missing from a run | `logs/pipeline.log` | Geocoding or weather fetch failed. The town keeps its old watermark, so a later run retries it |
| A run ends with `failed` | `error_message` in `etl_run_log` | The transaction rolled back. No fact, dimension, or watermark row from that run was kept |
| Rows appear in `rejected_records` | `reason` column | The day broke a validation rule in the table above |
| `bash: cron: command not found` | The scheduler host | Cron is not installed. Install it before using the entry above |

## Acceptance checks

- The first run loads every published day from 1 January 2025 for all five towns.
- A second run on the same day rereads only the three-day late window and adds no duplicate rows.
- The Mombasa delete-and-reload test loads exactly seven days.
- The monthly fact matches the daily fact after the late-window reread.
- No fact row has a date newer than five days before today.
- A row with maximum below minimum lands in `rejected_records`.
- With Open-Meteo blocked and the database reachable, the run log records a failed run with an error message.
- For every town, extracted equals loaded plus rejected.
- No password appears in code, logs, or version control.
- The SQL in `sql/business_questions.sql` answers the three questions.

## Stretch goals

All four stretch goals in the brief are implemented.

- `--add-city NAME` geocodes and backfills a sixth town. Later runs read it from `dim_city`.
- `--full-refresh` ignores watermarks and reloads every published day.
- `monthly_city_weather` stores total rainfall, rainy days, and average temperature for one town in one month. Each run rebuilds it from the daily fact with an upsert.
- Each run rereads the last three published days. The upsert on town and date replaces those rows, so a revision cannot add a duplicate.

## Credit

Weather and geocoding data come from Open-Meteo. The endpoints and the five-day delay follow the Open-Meteo documentation checked on 18 November 2026.
