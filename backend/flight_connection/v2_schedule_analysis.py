"""Research-only V2 schedule stability, compaction, reconstruction, and lookup tools."""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

SOURCE_DATABASE = Path("data/processed/flights_v2_research.duckdb")
EXACT_DATABASE = Path("data/processed/flights_v2_lookup.duckdb")
PATTERN_DATABASE = Path("data/processed/flights_v2_schedule_patterns.duckdb")
SUMMARY_PATH = Path("data/processed/v2_phase2_summary.json")
SOURCE_ROWS = 20_928_579
PHASE1_BYTES = 1_926_508_544
APPROXIMATE_GAP_DAYS = 21
VALID_OPERATING_CLASSES = {
    "daily", "weekdays_only", "weekends_only", "selected_weekdays", "seasonal", "irregular"
}


def circular_minute_distance(left: int, right: int) -> int:
    """Shortest distance between two minutes on a 24-hour clock."""
    if not 0 <= left <= 1439 or not 0 <= right <= 1439:
        raise ValueError("minutes must be between 0 and 1439")
    difference = abs(left - right)
    return min(difference, 1440 - difference)


def weekday_mask(weekdays: list[int] | tuple[int, ...] | set[int]) -> int:
    """Encode ISO weekdays 1..7 as a deterministic seven-bit mask."""
    mask = 0
    for weekday in weekdays:
        if weekday not in range(1, 8):
            raise ValueError("ISO weekdays must be between 1 and 7")
        mask |= 1 << (weekday - 1)
    return mask


def mask_includes(mask: int, weekday: int) -> bool:
    if not 0 <= mask <= 127:
        raise ValueError("weekday mask must be between 0 and 127")
    if weekday not in range(1, 8):
        raise ValueError("ISO weekday must be between 1 and 7")
    return bool(mask & (1 << (weekday - 1)))


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''").replace("\\", "/")


def _remove_existing(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()


def _validate_lookup_inputs(
    carrier: str, flight_number: str, flight_date: date | str,
    origin: str | None, destination: str | None, scheduled_departure_minutes: int | None,
) -> tuple[str, str, str, str | None, str | None, int | None]:
    normalized_carrier = carrier.strip().upper()
    normalized_number = flight_number.strip()
    normalized_date = str(flight_date)
    if not normalized_carrier or not normalized_number:
        raise ValueError("carrier and flight_number are required")
    try:
        date.fromisoformat(normalized_date)
    except ValueError as error:
        raise ValueError("flight_date must be ISO YYYY-MM-DD") from error
    normalized_origin = origin.strip().upper() if origin is not None else None
    normalized_destination = destination.strip().upper() if destination is not None else None
    for label, value in (("origin", normalized_origin), ("destination", normalized_destination)):
        if value is not None and (len(value) != 3 or not value.isalpha()):
            raise ValueError(f"{label} must be a three-letter airport code")
    if scheduled_departure_minutes is not None and not 0 <= scheduled_departure_minutes <= 1439:
        raise ValueError("scheduled_departure_minutes must be between 0 and 1439")
    return (
        normalized_carrier, normalized_number, normalized_date, normalized_origin,
        normalized_destination, scheduled_departure_minutes,
    )


def _classify_segments(segments: list[dict[str, Any]]) -> dict[str, Any]:
    classification = (
        "no_match" if not segments else "unique_match" if len(segments) == 1 else "multiple_segments"
    )
    return {"classification": classification, "match_count": len(segments), "segments": segments}


def lookup_exact(
    database: Path | str, *, carrier: str, flight_date: date | str, flight_number: str,
    origin: str | None = None, destination: str | None = None,
    scheduled_departure_minutes: int | None = None,
) -> dict[str, Any]:
    values = _validate_lookup_inputs(
        carrier, flight_number, flight_date, origin, destination, scheduled_departure_minutes
    )
    carrier, flight_number, flight_date, origin, destination, scheduled_departure_minutes = values
    clauses = ["reporting_carrier = ?", "flight_date = ?", "flight_number = ?"]
    params: list[Any] = [carrier, flight_date, flight_number]
    for column, value in (("origin", origin), ("destination", destination)):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    if scheduled_departure_minutes is not None:
        clauses.append("crs_departure_minutes = ?")
        params.append(scheduled_departure_minutes)
    with duckdb.connect(str(database), read_only=True) as connection:
        cursor = connection.execute(f"""
            SELECT flight_date, reporting_carrier, flight_number, origin, destination,
                   crs_departure_minutes, crs_arrival_minutes
            FROM exact_flights WHERE {' AND '.join(clauses)}
            ORDER BY crs_departure_minutes, crs_arrival_minutes, origin, destination
        """, params)
        names = [column[0] for column in cursor.description]
        segments = [dict(zip(names, row)) for row in cursor.fetchall()]
    return _classify_segments(segments)


def lookup_patterns(
    database: Path | str, *, table: str, carrier: str, flight_date: date | str,
    flight_number: str, origin: str | None = None, destination: str | None = None,
    scheduled_departure_minutes: int | None = None,
) -> dict[str, Any]:
    if table not in {"exact_schedule_periods", "pattern_schedule_periods"}:
        raise ValueError("unsupported pattern table")
    values = _validate_lookup_inputs(
        carrier, flight_number, flight_date, origin, destination, scheduled_departure_minutes
    )
    carrier, flight_number, flight_date, origin, destination, scheduled_departure_minutes = values
    clauses = [
        "reporting_carrier = ?", "flight_number = ?", "?::DATE BETWEEN valid_from AND valid_to",
        "(weekday_mask & (1 << (isodow(?::DATE) - 1))) != 0",
    ]
    params: list[Any] = [carrier, flight_number, flight_date, flight_date]
    for column, value in (("origin", origin), ("destination", destination)):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    if scheduled_departure_minutes is not None:
        clauses.append("crs_departure_minutes = ?")
        params.append(scheduled_departure_minutes)
    with duckdb.connect(str(database), read_only=True) as connection:
        cursor = connection.execute(f"""
            SELECT ?::DATE AS flight_date, reporting_carrier, flight_number, origin, destination,
                   crs_departure_minutes, crs_arrival_minutes
            FROM {table} WHERE {' AND '.join(clauses)}
            ORDER BY crs_departure_minutes, crs_arrival_minutes, origin, destination
        """, [flight_date, *params])
        names = [column[0] for column in cursor.description]
        segments = [dict(zip(names, row)) for row in cursor.fetchall()]
    return _classify_segments(segments)


def _build_exact_database(source: Path, destination: Path) -> dict[str, Any]:
    started = time.perf_counter()
    _remove_existing(destination)
    with duckdb.connect(str(destination)) as connection:
        connection.execute(f"ATTACH '{_sql_path(source)}' AS source (READ_ONLY)")
        connection.execute("""
            CREATE TABLE exact_flights AS
            SELECT flight_date, reporting_carrier, flight_number, origin, destination,
                   crs_departure_minutes, crs_arrival_minutes
            FROM source.v2_flight_records WHERE flight_number IS NOT NULL
            ORDER BY reporting_carrier, flight_date, flight_number,
                     crs_departure_minutes, crs_arrival_minutes, origin, destination
        """)
        connection.execute("""
            CREATE INDEX exact_lookup_idx ON exact_flights
            (reporting_carrier, flight_date, flight_number)
        """)
        row_count, start_date, end_date = connection.execute(
            "SELECT count(*), min(flight_date), max(flight_date) FROM exact_flights"
        ).fetchone()
        connection.execute("CHECKPOINT")
    return {
        "row_count": int(row_count), "date_start": str(start_date), "date_end": str(end_date),
        "database_bytes": destination.stat().st_size,
        "runtime_seconds": round(time.perf_counter() - started, 3),
    }


def _build_pattern_database(exact_database: Path, destination: Path) -> dict[str, Any]:
    started = time.perf_counter()
    _remove_existing(destination)
    with duckdb.connect(str(destination)) as connection:
        connection.execute(f"ATTACH '{_sql_path(exact_database)}' AS exact (READ_ONLY)")
        connection.execute("""
            CREATE TEMP TABLE weekly_signatures AS
            SELECT reporting_carrier, flight_number, origin, destination,
                   crs_departure_minutes, crs_arrival_minutes,
                   date_trunc('week', flight_date)::DATE AS week_start,
                   min(flight_date) AS observed_from, max(flight_date) AS observed_to,
                   sum(DISTINCT (1 << (isodow(flight_date) - 1)))::UTINYINT AS weekday_mask,
                   count(*)::INTEGER AS source_observations
            FROM exact.exact_flights GROUP BY ALL
        """)
        connection.execute("""
            CREATE TABLE exact_schedule_periods AS
            WITH marked AS (
                SELECT *, CASE WHEN
                    lag(week_start) OVER signature_order = week_start - INTERVAL 7 DAY
                    AND lag(weekday_mask) OVER signature_order = weekday_mask
                    THEN 0 ELSE 1 END AS starts_period
                FROM weekly_signatures
                WINDOW signature_order AS (
                    PARTITION BY reporting_carrier, flight_number, origin, destination,
                                 crs_departure_minutes, crs_arrival_minutes
                    ORDER BY week_start
                )
            ), grouped AS (
                SELECT *, sum(starts_period) OVER (
                    PARTITION BY reporting_carrier, flight_number, origin, destination,
                                 crs_departure_minutes, crs_arrival_minutes
                    ORDER BY week_start ROWS UNBOUNDED PRECEDING
                ) AS period_id FROM marked
            )
            SELECT reporting_carrier, flight_number, origin, destination,
                   crs_departure_minutes, crs_arrival_minutes,
                   min(observed_from) AS valid_from, max(observed_to) AS valid_to,
                   weekday_mask, sum(source_observations)::INTEGER AS source_observations,
                   count(*)::INTEGER AS weeks
            FROM grouped GROUP BY reporting_carrier, flight_number, origin, destination,
                                  crs_departure_minutes, crs_arrival_minutes, weekday_mask, period_id
            ORDER BY reporting_carrier, flight_number, valid_from,
                     crs_departure_minutes, origin, destination
        """)
        connection.execute(f"""
            CREATE TABLE pattern_schedule_periods AS
            WITH ordered AS (
                SELECT *, CASE WHEN lag(flight_date) OVER signature_order IS NULL
                    OR date_diff('day', lag(flight_date) OVER signature_order, flight_date)
                       > {APPROXIMATE_GAP_DAYS}
                    THEN 1 ELSE 0 END AS starts_period
                FROM exact.exact_flights
                WINDOW signature_order AS (
                    PARTITION BY reporting_carrier, flight_number, origin, destination,
                                 crs_departure_minutes, crs_arrival_minutes
                    ORDER BY flight_date
                )
            ), grouped AS (
                SELECT *, sum(starts_period) OVER (
                    PARTITION BY reporting_carrier, flight_number, origin, destination,
                                 crs_departure_minutes, crs_arrival_minutes
                    ORDER BY flight_date ROWS UNBOUNDED PRECEDING
                ) AS period_id FROM ordered
            )
            SELECT reporting_carrier, flight_number, origin, destination,
                   crs_departure_minutes, crs_arrival_minutes,
                   min(flight_date) AS valid_from, max(flight_date) AS valid_to,
                   sum(DISTINCT (1 << (isodow(flight_date) - 1)))::UTINYINT AS weekday_mask,
                   count(*)::INTEGER AS source_observations,
                   max(date_diff('day', lag_date, flight_date))::INTEGER AS maximum_internal_gap_days
            FROM (
                SELECT *, lag(flight_date) OVER (
                    PARTITION BY reporting_carrier, flight_number, origin, destination,
                                 crs_departure_minutes, crs_arrival_minutes, period_id
                    ORDER BY flight_date
                ) AS lag_date FROM grouped
            ) GROUP BY reporting_carrier, flight_number, origin, destination,
                       crs_departure_minutes, crs_arrival_minutes, period_id
            ORDER BY reporting_carrier, flight_number, valid_from,
                     crs_departure_minutes, origin, destination
        """)
        connection.execute("""
            CREATE TABLE pattern_exclusions AS
            WITH expanded AS (
                SELECT d.day::DATE AS flight_date, reporting_carrier, flight_number,
                       origin, destination, crs_departure_minutes, crs_arrival_minutes
                FROM pattern_schedule_periods,
                     LATERAL generate_series(valid_from, valid_to, INTERVAL 1 DAY) d(day)
                WHERE (weekday_mask & (1 << (isodow(d.day) - 1))) != 0
            )
            SELECT * FROM expanded EXCEPT SELECT * FROM exact.exact_flights
            ORDER BY reporting_carrier, flight_date, flight_number,
                     crs_departure_minutes, origin, destination
        """)
        _create_stability_tables(connection)
        for table in ("exact_schedule_periods", "pattern_schedule_periods"):
            connection.execute(f"""
                CREATE INDEX {table}_lookup_idx ON {table}
                (reporting_carrier, flight_number, valid_from, valid_to)
            """)
        connection.execute("""
            CREATE INDEX pattern_exclusions_lookup_idx ON pattern_exclusions
            (reporting_carrier, flight_date, flight_number)
        """)
        exact_count = connection.execute("SELECT count(*) FROM exact_schedule_periods").fetchone()[0]
        pattern_count = connection.execute("SELECT count(*) FROM pattern_schedule_periods").fetchone()[0]
        exclusion_count = connection.execute("SELECT count(*) FROM pattern_exclusions").fetchone()[0]
        period_range = connection.execute(
            "SELECT min(valid_from), max(valid_to) FROM exact_schedule_periods"
        ).fetchone()
        connection.execute("CHECKPOINT")
    return {
        "exact_period_rows": int(exact_count), "pattern_period_rows": int(pattern_count),
        "pattern_exclusion_rows": int(exclusion_count),
        "date_start": str(period_range[0]), "date_end": str(period_range[1]),
        "database_bytes": destination.stat().st_size,
        "runtime_seconds": round(time.perf_counter() - started, 3),
    }


def _create_stability_tables(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute("""
        CREATE TABLE route_stability AS
        WITH route_dates AS (
            SELECT reporting_carrier, flight_number, origin, destination,
                   count(DISTINCT flight_date) AS route_dates
            FROM exact.exact_flights GROUP BY ALL
        ), ranked AS (
            SELECT *, row_number() OVER (
                PARTITION BY reporting_carrier, flight_number
                ORDER BY route_dates DESC, origin, destination
            ) AS route_rank FROM route_dates
        ), date_sets AS (
            SELECT reporting_carrier, flight_number, flight_date,
                   bit_xor(hash(origin, destination)) AS route_set,
                   count(*) AS segments
            FROM exact.exact_flights GROUP BY 1, 2, 3
        ), changes AS (
            SELECT *, lag(route_set) OVER (
                PARTITION BY reporting_carrier, flight_number ORDER BY flight_date
            ) AS previous_route_set FROM date_sets
        ), totals AS (
            SELECT reporting_carrier, flight_number, count(*) AS operating_dates,
                   count(DISTINCT route_set) AS distinct_daily_route_sets,
                   count(*) FILTER (WHERE segments > 1) AS multi_segment_dates,
                   count(*) FILTER (
                       WHERE previous_route_set IS NOT NULL AND route_set != previous_route_set
                   ) AS route_set_changes
            FROM changes GROUP BY 1, 2
        ), route_summary AS (
            SELECT reporting_carrier, flight_number, count(*) AS distinct_routes,
                   max(route_dates) AS dominant_route_dates,
                   max(CASE WHEN route_rank = 1 THEN origin || '-' || destination END)
                       AS dominant_route
            FROM ranked GROUP BY 1, 2
        ), calendar AS (
            SELECT reporting_carrier, flight_number,
                   count(DISTINCT hash(year(flight_date), origin, destination)) AS year_routes,
                   count(DISTINCT hash(year(flight_date), month(flight_date), origin, destination))
                       AS month_routes
            FROM exact.exact_flights GROUP BY 1, 2
        )
        SELECT totals.*, route_summary.distinct_routes,
               route_summary.dominant_route_dates,
               route_summary.dominant_route_dates / operating_dates::DOUBLE
                   AS dominant_route_date_share,
               route_summary.dominant_route,
               multi_segment_dates > 0 AS has_same_date_multi_segment,
               calendar.year_routes, calendar.month_routes
        FROM totals JOIN route_summary USING (reporting_carrier, flight_number)
        JOIN calendar USING (reporting_carrier, flight_number)
    """)
    connection.execute("""
        CREATE TEMP TABLE schedule_modes AS
        WITH counts AS (
            SELECT reporting_carrier, flight_number, origin, destination,
                   crs_departure_minutes, crs_arrival_minutes, count(*) AS n
            FROM exact.exact_flights GROUP BY ALL
        )
        SELECT * FROM counts QUALIFY row_number() OVER (
            PARTITION BY reporting_carrier, flight_number, origin, destination
            ORDER BY n DESC, crs_departure_minutes, crs_arrival_minutes
        ) = 1
    """)
    connection.execute("""
        CREATE TABLE schedule_stability AS
        WITH enriched AS (
            SELECT flights.*,
                   modes.crs_departure_minutes AS modal_departure_minutes,
                   modes.crs_arrival_minutes AS modal_arrival_minutes,
                   modes.n AS modal_pair_records,
                   least(abs(flights.crs_departure_minutes - modes.crs_departure_minutes),
                         1440 - abs(flights.crs_departure_minutes - modes.crs_departure_minutes))
                       AS departure_deviation,
                   least(abs(flights.crs_arrival_minutes - modes.crs_arrival_minutes),
                         1440 - abs(flights.crs_arrival_minutes - modes.crs_arrival_minutes))
                       AS arrival_deviation,
                   lag(flights.crs_departure_minutes || ':' || flights.crs_arrival_minutes) OVER (
                       PARTITION BY flights.reporting_carrier, flights.flight_number,
                                    flights.origin, flights.destination
                       ORDER BY flights.flight_date
                   ) AS previous_times
            FROM exact.exact_flights flights JOIN schedule_modes modes USING
                (reporting_carrier, flight_number, origin, destination)
        )
        SELECT reporting_carrier, flight_number, origin, destination, count(*) AS records,
               count(DISTINCT flight_date) AS operating_dates,
               count(DISTINCT crs_departure_minutes) AS distinct_departure_minutes,
               count(DISTINCT crs_arrival_minutes) AS distinct_arrival_minutes,
               count(DISTINCT crs_departure_minutes || ':' || crs_arrival_minutes) AS distinct_time_pairs,
               max(modal_departure_minutes) AS modal_departure_minutes,
               max(modal_arrival_minutes) AS modal_arrival_minutes,
               max(modal_pair_records) / count(*)::DOUBLE AS modal_time_pair_share,
               median(departure_deviation) AS median_departure_deviation,
               quantile_cont(departure_deviation, 0.9) AS p90_departure_deviation,
               median(arrival_deviation) AS median_arrival_deviation,
               quantile_cont(arrival_deviation, 0.9) AS p90_arrival_deviation,
               count(*) FILTER (
                   WHERE previous_times IS NOT NULL
                     AND previous_times != crs_departure_minutes || ':' || crs_arrival_minutes
               ) AS schedule_changes
        FROM enriched GROUP BY 1, 2, 3, 4
    """)
    connection.execute("""
        CREATE TABLE operating_patterns AS
        WITH spans AS (
            SELECT reporting_carrier, flight_number, origin, destination,
                   min(flight_date) AS first_date, max(flight_date) AS last_date,
                   count(DISTINCT flight_date) AS operating_dates,
                   count(DISTINCT month(flight_date)) AS active_months,
                   max(gap_days) AS maximum_gap_days
            FROM (
                SELECT *, date_diff('day', lag(flight_date) OVER (
                    PARTITION BY reporting_carrier, flight_number, origin, destination
                    ORDER BY flight_date
                ), flight_date) AS gap_days
                FROM exact.exact_flights
            ) GROUP BY 1, 2, 3, 4
        ), weekday_counts AS (
            SELECT reporting_carrier, flight_number, origin, destination, isodow(flight_date) AS dow,
                   count(DISTINCT flight_date) AS operated
            FROM exact.exact_flights GROUP BY 1, 2, 3, 4, 5
        ), masks AS (
            SELECT counts.reporting_carrier, counts.flight_number, counts.origin, counts.destination,
                   sum(CASE WHEN counts.operated / greatest(eligible.eligible, 1)::DOUBLE >= 0.5
                            THEN (1 << (counts.dow - 1)) ELSE 0 END)::UTINYINT AS active_weekday_mask
            FROM weekday_counts counts JOIN spans USING
                (reporting_carrier, flight_number, origin, destination)
            CROSS JOIN LATERAL (
                SELECT count(*) AS eligible FROM generate_series(first_date, last_date, INTERVAL 1 DAY) d(day)
                WHERE isodow(day) = counts.dow
            ) eligible GROUP BY 1, 2, 3, 4
        )
        SELECT spans.*, masks.active_weekday_mask,
               operating_dates / greatest(date_diff('day', first_date, last_date) + 1, 1)::DOUBLE
                   AS calendar_density,
               CASE
                   WHEN operating_dates < 8 OR operating_dates /
                        greatest(date_diff('day', first_date, last_date) + 1, 1)::DOUBLE < 0.20
                       THEN 'irregular'
                   WHEN maximum_gap_days > 35 OR active_months <= 6 THEN 'seasonal'
                   WHEN active_weekday_mask = 127 THEN 'daily'
                   WHEN active_weekday_mask = 31 THEN 'weekdays_only'
                   WHEN active_weekday_mask = 96 THEN 'weekends_only'
                   ELSE 'selected_weekdays'
               END AS operating_pattern
        FROM spans JOIN masks USING (reporting_carrier, flight_number, origin, destination)
    """)


def _accuracy_metrics(exact_database: Path, pattern_database: Path) -> dict[str, Any]:
    with duckdb.connect(str(pattern_database), read_only=False) as connection:
        connection.execute(f"ATTACH '{_sql_path(exact_database)}' AS exact (READ_ONLY)")
        result: dict[str, Any] = {}
        source_count = int(connection.execute("SELECT count(*) FROM exact.exact_flights").fetchone()[0])
        connection.execute("""
            CREATE TEMP TABLE source_keys AS SELECT DISTINCT
                reporting_carrier, flight_number, flight_date FROM exact.exact_flights
        """)
        connection.execute("""
            CREATE TEMP TABLE source_routes AS SELECT DISTINCT
                reporting_carrier, flight_number, flight_date, origin, destination
            FROM exact.exact_flights
        """)
        for label, table in (("exact", "exact_schedule_periods"), ("pattern", "pattern_schedule_periods")):
            connection.execute(f"""
                CREATE OR REPLACE TEMP TABLE expanded AS
                SELECT d.day::DATE AS flight_date, reporting_carrier, flight_number, origin,
                       destination, crs_departure_minutes, crs_arrival_minutes
                FROM {table}, LATERAL generate_series(valid_from, valid_to, INTERVAL 1 DAY) d(day)
                WHERE (weekday_mask & (1 << (isodow(d.day) - 1))) != 0
            """)
            if label == "pattern":
                connection.execute("""
                    CREATE OR REPLACE TEMP TABLE false_rows AS SELECT * FROM pattern_exclusions
                """)
            else:
                connection.execute("""
                    CREATE OR REPLACE TEMP TABLE false_rows AS
                    SELECT * FROM expanded EXCEPT SELECT * FROM exact.exact_flights
                """)
            expanded = int(connection.execute("SELECT count(*) FROM expanded").fetchone()[0])
            false_positive = int(connection.execute("SELECT count(*) FROM false_rows").fetchone()[0])
            false_negative = int(connection.execute("""
                SELECT count(*) FROM (
                    SELECT * FROM exact.exact_flights EXCEPT SELECT * FROM expanded
                )
            """).fetchone()[0])
            route_mismatch, time_mismatch, extra_date = map(int, connection.execute("""
                SELECT count(*) FILTER (WHERE keys.reporting_carrier IS NOT NULL
                                          AND routes.reporting_carrier IS NULL),
                       count(*) FILTER (WHERE routes.reporting_carrier IS NOT NULL),
                       count(*) FILTER (WHERE keys.reporting_carrier IS NULL)
                FROM false_rows errors
                LEFT JOIN source_keys keys USING (reporting_carrier, flight_number, flight_date)
                LEFT JOIN source_routes routes USING
                    (reporting_carrier, flight_number, flight_date, origin, destination)
            """).fetchone())
            result[label] = {
                "expanded_rows": expanded, "exact_matches": expanded - false_positive,
                "false_positive_rows": false_positive, "false_negative_rows": false_negative,
                "route_mismatch_candidates": route_mismatch,
                "schedule_time_mismatch_candidates": time_mismatch,
                "false_positive_nonoperating_dates": extra_date,
                "source_recall": round((source_count - false_negative) / source_count, 9),
                "expanded_precision": round((expanded - false_positive) / expanded, 9),
            }
    return result


def _aggregate_findings(pattern_database: Path) -> dict[str, Any]:
    with duckdb.connect(str(pattern_database), read_only=True) as connection:
        route = connection.execute("""
            SELECT count(*), count(*) FILTER (WHERE distinct_routes=1),
                   count(*) FILTER (WHERE distinct_routes>1),
                   count(*) FILTER (WHERE dominant_route_date_share>=0.90),
                   count(*) FILTER (WHERE dominant_route_date_share>=0.95),
                   count(*) FILTER (WHERE dominant_route_date_share>=0.99),
                   count(*) FILTER (WHERE dominant_route_date_share=1.0),
                   median(distinct_routes), quantile_cont(distinct_routes,0.9),
                   median(route_set_changes), quantile_cont(route_set_changes,0.9),
                   count(*) FILTER (WHERE has_same_date_multi_segment)
            FROM route_stability
        """).fetchone()
        total = int(route[0])
        schedule = connection.execute("""
            SELECT count(*), count(*) FILTER (WHERE distinct_time_pairs=1),
                   count(*) FILTER (WHERE distinct_time_pairs>1),
                   count(*) FILTER (WHERE modal_time_pair_share>=0.90),
                   count(*) FILTER (WHERE modal_time_pair_share>=0.95),
                   count(*) FILTER (WHERE modal_time_pair_share>=0.99),
                   count(*) FILTER (WHERE modal_time_pair_share=1.0),
                   median(distinct_time_pairs), quantile_cont(distinct_time_pairs,0.9),
                   median(median_departure_deviation), quantile_cont(p90_departure_deviation,0.9),
                   median(schedule_changes), quantile_cont(schedule_changes,0.9)
            FROM schedule_stability
        """).fetchone()
        schedule_total = int(schedule[0])
        patterns = dict(connection.execute(
            "SELECT operating_pattern,count(*) FROM operating_patterns GROUP BY 1 ORDER BY 1"
        ).fetchall())
        examples = {
            "stable": connection.execute("""
                SELECT reporting_carrier,flight_number,dominant_route,operating_dates,
                       dominant_route_date_share FROM route_stability
                WHERE distinct_routes=1 AND operating_dates>=900 ORDER BY operating_dates DESC LIMIT 1
            """).fetchone(),
            "route_change": connection.execute("""
                SELECT reporting_carrier,flight_number,dominant_route,distinct_routes,
                       dominant_route_date_share,route_set_changes FROM route_stability
                WHERE distinct_routes>1 AND NOT has_same_date_multi_segment AND operating_dates>=100
                ORDER BY route_set_changes DESC,operating_dates DESC LIMIT 1
            """).fetchone(),
            "schedule_change": connection.execute("""
                SELECT reporting_carrier,flight_number,origin,destination,records,
                       distinct_time_pairs,modal_departure_minutes,modal_arrival_minutes,
                       modal_time_pair_share,schedule_changes FROM schedule_stability
                WHERE records>=100 AND distinct_time_pairs>1
                ORDER BY schedule_changes DESC,records DESC LIMIT 1
            """).fetchone(),
            "multi_segment": connection.execute("""
                SELECT reporting_carrier,flight_number,operating_dates,distinct_routes,
                       multi_segment_dates,dominant_route FROM route_stability
                WHERE has_same_date_multi_segment
                ORDER BY multi_segment_dates DESC,distinct_routes DESC LIMIT 1
            """).fetchone(),
        }
    route_names = (
        "combinations", "single_route", "multiple_routes", "dominant_ge_90", "dominant_ge_95",
        "dominant_ge_99", "dominant_100", "median_routes", "p90_routes",
        "median_route_set_changes", "p90_route_set_changes", "same_date_multi_segment",
    )
    schedule_names = (
        "route_combinations", "single_time_pair", "multiple_time_pairs", "modal_ge_90",
        "modal_ge_95", "modal_ge_99", "modal_100", "median_time_pairs", "p90_time_pairs",
        "median_of_median_departure_deviation", "p90_of_route_p90_departure_deviation",
        "median_schedule_changes", "p90_schedule_changes",
    )
    route_result = dict(zip(route_names, route))
    schedule_result = dict(zip(schedule_names, schedule))
    for result, denominator, keys in (
        (route_result, total, ("single_route", "multiple_routes", "dominant_ge_90", "dominant_ge_95",
                               "dominant_ge_99", "dominant_100", "same_date_multi_segment")),
        (schedule_result, schedule_total, ("single_time_pair", "multiple_time_pairs", "modal_ge_90",
                                           "modal_ge_95", "modal_ge_99", "modal_100")),
    ):
        for key in keys:
            result[f"{key}_percent"] = round(100 * int(result[key]) / denominator, 6)
    return {
        "route": route_result, "schedule": schedule_result,
        "operating_patterns": {key: int(value) for key, value in patterns.items()},
        "examples": {key: list(value) if value else None for key, value in examples.items()},
    }


def _conditional_schedule_accuracy(exact_database: Path) -> dict[str, float]:
    with duckdb.connect(str(exact_database), read_only=True) as connection:
        base = "reporting_carrier,flight_number,origin,destination"
        result: dict[str, float] = {}
        dimensions = (
            ("global", "", ""),
            ("weekday", ",isodow(flight_date) AS dimension", ",dimension"),
            ("month", ",month(flight_date) AS dimension", ",dimension"),
            ("year", ",year(flight_date) AS dimension", ",dimension"),
        )
        for label, extra_select, extra_partition in dimensions:
            row = connection.execute(f"""
                WITH counts AS (
                    SELECT {base}{extra_select},crs_departure_minutes,crs_arrival_minutes,count(*) n
                    FROM exact_flights GROUP BY ALL
                ), modes AS (
                    SELECT * FROM counts QUALIFY row_number() OVER (
                        PARTITION BY {base}{extra_partition}
                        ORDER BY n DESC,crs_departure_minutes,crs_arrival_minutes
                    )=1
                ) SELECT sum(n)/(SELECT count(*) FROM exact_flights)::DOUBLE FROM modes
            """).fetchone()[0]
            result[f"{label}_modal_accuracy"] = round(float(row), 9)
        result["weekday_gain_pp"] = round(
            100 * (result["weekday_modal_accuracy"] - result["global_modal_accuracy"]), 6
        )
        result["month_gain_pp"] = round(
            100 * (result["month_modal_accuracy"] - result["global_modal_accuracy"]), 6
        )
        result["year_gain_pp"] = round(
            100 * (result["year_modal_accuracy"] - result["global_modal_accuracy"]), 6
        )
    return result


def _sample_reconstruction(
    exact_database: Path, pattern_database: Path, *, seed: int = 20260812,
) -> dict[str, Any]:
    """Deterministic stratified sample across calendar, carrier, and stability categories."""
    with duckdb.connect(str(pattern_database), read_only=False) as connection:
        connection.execute(f"ATTACH '{_sql_path(exact_database)}' AS exact_sample (READ_ONLY)")
        connection.execute(f"""
            CREATE OR REPLACE TEMP TABLE validation_keys AS
            WITH candidates AS (
                SELECT DISTINCT flights.reporting_carrier, flights.flight_number, flights.flight_date,
                       year(flights.flight_date) AS year, month(flights.flight_date) AS month,
                       isodow(flights.flight_date) AS dow,
                       routes.has_same_date_multi_segment,
                       schedules.distinct_time_pairs > 1 AS changing_schedule
                FROM exact_sample.exact_flights flights
                JOIN route_stability routes USING (reporting_carrier, flight_number)
                JOIN schedule_stability schedules USING
                    (reporting_carrier, flight_number, origin, destination)
            )
            SELECT reporting_carrier, flight_number, flight_date FROM candidates
            QUALIFY row_number() OVER (
                PARTITION BY reporting_carrier, year, month, dow,
                             has_same_date_multi_segment, changing_schedule
                ORDER BY hash(reporting_carrier, flight_number, flight_date, {seed})
            ) <= 2
        """)
        key_count = int(connection.execute("SELECT count(*) FROM validation_keys").fetchone()[0])
        source_count = int(connection.execute("""
            SELECT count(*) FROM exact_sample.exact_flights source JOIN validation_keys keys USING
                (reporting_carrier,flight_number,flight_date)
        """).fetchone()[0])
        output: dict[str, Any] = {"seed": seed, "sampled_keys": key_count, "source_segments": source_count}
        for label, table in (("exact", "exact_schedule_periods"), ("pattern", "pattern_schedule_periods")):
            row = connection.execute(f"""
                WITH reconstructed AS (
                    SELECT keys.flight_date, periods.reporting_carrier, periods.flight_number,
                           periods.origin, periods.destination, periods.crs_departure_minutes,
                           periods.crs_arrival_minutes
                    FROM validation_keys keys JOIN {table} periods
                      ON periods.reporting_carrier=keys.reporting_carrier
                     AND periods.flight_number=keys.flight_number
                     AND keys.flight_date BETWEEN periods.valid_from AND periods.valid_to
                     AND (periods.weekday_mask & (1 << (isodow(keys.flight_date)-1))) != 0
                ), source AS (
                    SELECT flights.* FROM exact_sample.exact_flights flights
                    JOIN validation_keys keys USING (reporting_carrier,flight_number,flight_date)
                ), false_rows AS (SELECT * FROM reconstructed EXCEPT SELECT * FROM source),
                missing_rows AS (SELECT * FROM source EXCEPT SELECT * FROM reconstructed)
                SELECT (SELECT count(*) FROM reconstructed), (SELECT count(*) FROM false_rows),
                       (SELECT count(*) FROM missing_rows)
            """).fetchone()
            reconstructed, false_positive, false_negative = map(int, row)
            output[label] = {
                "reconstructed_segments": reconstructed, "false_positive_rows": false_positive,
                "false_negative_rows": false_negative,
                "exact_source_row_accuracy": round(
                    (source_count - false_negative) / source_count, 9
                ),
            }
    return output


def _benchmark_lookups(
    source_database: Path, exact_database: Path, pattern_database: Path, *, repetitions: int = 200,
) -> dict[str, Any]:
    cases = {
        "unique": ("DL", "2025-06-15", "1234"),
        "multiple": ("WN", "2025-09-15", "39"),
        "no_match": ("DL", "2025-06-15", "99999"),
    }
    sql = {
        "full": """SELECT origin,destination,crs_departure_minutes,crs_arrival_minutes
                   FROM v2_flight_records WHERE reporting_carrier=? AND flight_date=?
                     AND flight_number=? ORDER BY crs_departure_minutes,crs_arrival_minutes,origin,destination""",
        "exact": """SELECT origin,destination,crs_departure_minutes,crs_arrival_minutes
                    FROM exact_flights WHERE reporting_carrier=? AND flight_date=?
                      AND flight_number=? ORDER BY crs_departure_minutes,crs_arrival_minutes,origin,destination""",
        "pattern": """SELECT origin,destination,crs_departure_minutes,crs_arrival_minutes
                      FROM pattern_schedule_periods WHERE reporting_carrier=? AND flight_number=?
                        AND ?::DATE BETWEEN valid_from AND valid_to
                        AND (weekday_mask & (1 << (isodow(?::DATE)-1))) != 0
                      ORDER BY crs_departure_minutes,crs_arrival_minutes,origin,destination""",
    }
    connections = {
        "full": duckdb.connect(str(source_database), read_only=True),
        "exact": duckdb.connect(str(exact_database), read_only=True),
        "pattern": duckdb.connect(str(pattern_database), read_only=True),
    }
    output: dict[str, Any] = {"repetitions_per_case": repetitions, "method": "warm reused read-only connection"}
    try:
        for representation, connection in connections.items():
            output[representation] = {}
            for case_name, (carrier, flight_date, flight_number) in cases.items():
                params = (
                    [carrier, flight_number, flight_date, flight_date]
                    if representation == "pattern" else [carrier, flight_date, flight_number]
                )
                connection.execute(sql[representation], params).fetchall()
                samples: list[float] = []
                for _ in range(repetitions):
                    started = time.perf_counter_ns()
                    rows = connection.execute(sql[representation], params).fetchall()
                    samples.append((time.perf_counter_ns() - started) / 1_000_000)
                ordered = sorted(samples)
                output[representation][case_name] = {
                    "rows": len(rows), "median_ms": round(statistics.median(samples), 4),
                    "p95_ms": round(ordered[math.ceil(0.95 * len(ordered)) - 1], 4),
                    "min_ms": round(min(samples), 4), "max_ms": round(max(samples), 4),
                }
    finally:
        for connection in connections.values():
            connection.close()
    return output


def build_phase2(
    *, source_database: Path = SOURCE_DATABASE, exact_database: Path = EXACT_DATABASE,
    pattern_database: Path = PATTERN_DATABASE, summary_path: Path = SUMMARY_PATH,
    benchmark_repetitions: int = 200,
) -> dict[str, Any]:
    if not source_database.exists():
        raise FileNotFoundError(f"Phase 1 research database is required: {source_database}")
    started = time.perf_counter()
    exact = _build_exact_database(source_database, exact_database)
    patterns = _build_pattern_database(exact_database, pattern_database)
    accuracy = _accuracy_metrics(exact_database, pattern_database)
    findings = _aggregate_findings(pattern_database)
    conditional = _conditional_schedule_accuracy(exact_database)
    sample = _sample_reconstruction(exact_database, pattern_database)
    benchmarks = _benchmark_lookups(
        source_database, exact_database, pattern_database, repetitions=benchmark_repetitions
    )
    result = {
        "source_rows": SOURCE_ROWS, "phase1_bytes": PHASE1_BYTES,
        "exact": exact, "patterns": patterns, "accuracy": accuracy,
        "findings": findings, "conditional_schedule_accuracy": conditional,
        "sample_reconstruction": sample, "benchmarks": benchmarks,
        "total_runtime_seconds": round(time.perf_counter() - started, 3),
    }
    for artifact in (exact, patterns):
        artifact["row_compression_ratio"] = round(SOURCE_ROWS / (
            artifact.get("row_count") or artifact.get("exact_period_rows")
        ), 6)
        artifact["size_percent_of_phase1"] = round(100 * artifact["database_bytes"] / PHASE1_BYTES, 6)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_DATABASE)
    parser.add_argument("--exact", type=Path, default=EXACT_DATABASE)
    parser.add_argument("--patterns", type=Path, default=PATTERN_DATABASE)
    parser.add_argument("--summary", type=Path, default=SUMMARY_PATH)
    parser.add_argument("--benchmark-repetitions", type=int, default=200)
    args = parser.parse_args()
    if args.benchmark_repetitions < 1:
        parser.error("--benchmark-repetitions must be positive")
    result = build_phase2(
        source_database=args.source, exact_database=args.exact, pattern_database=args.patterns,
        summary_path=args.summary, benchmark_repetitions=args.benchmark_repetitions,
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
