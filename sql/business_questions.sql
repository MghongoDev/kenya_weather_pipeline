-- Business questions for the advisory team.

-- 1. Wettest town in each calendar month. Ties go to the town first in name order.
WITH monthly AS (
    SELECT
        f.city_id,
        c.name AS city,
        DATE_TRUNC('month', f.weather_date)::date AS month_start,
        SUM(f.precipitation) AS total_rain_mm
    FROM fact_daily_weather AS f
    JOIN dim_city AS c USING (city_id)
    GROUP BY f.city_id, c.name, month_start
), ranked AS (
    SELECT
        monthly.*,
        ROW_NUMBER() OVER (
            PARTITION BY month_start
            ORDER BY total_rain_mm DESC, city
        ) AS position
    FROM monthly
)
SELECT
    TO_CHAR(month_start, 'YYYY-MM') AS month,
    city,
    ROUND(total_rain_mm, 1) AS total_rain_mm
FROM ranked
WHERE position = 1
ORDER BY month;

-- 2. Rainy days for each town in each Kenyan season.
SELECT
    c.name AS city,
    d.season,
    COUNT(*) AS days_observed,
    COUNT(*) FILTER (WHERE f.is_rainy_day) AS rainy_days
FROM fact_daily_weather AS f
JOIN dim_city AS c USING (city_id)
JOIN dim_date AS d USING (weather_date)
GROUP BY c.name, d.season
ORDER BY c.name, d.season;

-- 3. Hottest and coldest day recorded for each town. Ties go to the earlier date.
WITH hottest AS (
    SELECT DISTINCT ON (f.city_id)
        f.city_id, f.weather_date, f.temp_max AS temp_c
    FROM fact_daily_weather AS f
    ORDER BY f.city_id, f.temp_max DESC, f.weather_date
), coldest AS (
    SELECT DISTINCT ON (f.city_id)
        f.city_id, f.weather_date, f.temp_min AS temp_c
    FROM fact_daily_weather AS f
    ORDER BY f.city_id, f.temp_min ASC, f.weather_date
)
SELECT c.name AS city, 'hottest' AS kind, h.weather_date, h.temp_c
FROM hottest AS h
JOIN dim_city AS c USING (city_id)
UNION ALL
SELECT c.name AS city, 'coldest' AS kind, l.weather_date, l.temp_c
FROM coldest AS l
JOIN dim_city AS c USING (city_id)
ORDER BY city, kind;

-- Stretch goal: read the stored monthly fact instead of aggregating the daily fact.
SELECT
    c.name AS city,
    TO_CHAR(m.month_start, 'YYYY-MM') AS month,
    m.total_rain_mm,
    m.rainy_days,
    m.avg_temp_c
FROM monthly_city_weather AS m
JOIN dim_city AS c USING (city_id)
ORDER BY c.name, m.month_start;
