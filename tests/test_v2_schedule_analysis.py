from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest

from backend.flight_connection.v2_schedule_analysis import (
    _accuracy_metrics, _build_exact_database, _build_pattern_database,
    circular_minute_distance, lookup_exact, lookup_patterns, mask_includes, weekday_mask,
)


def create_source(path: Path, rows: list[tuple]) -> None:
    with duckdb.connect(str(path)) as connection:
        connection.execute("""
            CREATE TABLE v2_flight_records (
                flight_date DATE, reporting_carrier VARCHAR, flight_number VARCHAR,
                origin VARCHAR, destination VARCHAR, crs_departure_minutes SMALLINT,
                crs_arrival_minutes SMALLINT
            )
        """)
        connection.executemany("INSERT INTO v2_flight_records VALUES (?,?,?,?,?,?,?)", rows)


def build(tmp_path: Path, rows: list[tuple]) -> tuple[Path, Path]:
    source = tmp_path / "source.duckdb"
    exact = tmp_path / "exact.duckdb"
    patterns = tmp_path / "patterns.duckdb"
    create_source(source, rows)
    _build_exact_database(source, exact)
    _build_pattern_database(exact, patterns)
    return exact, patterns


def test_circular_time_distance_handles_midnight_and_invalid_values():
    assert circular_minute_distance(23 * 60 + 55, 5) == 10
    assert circular_minute_distance(60, 120) == 60
    with pytest.raises(ValueError, match="between 0 and 1439"):
        circular_minute_distance(1440, 0)


def test_weekday_masks_are_deterministic_and_validated():
    mask = weekday_mask([1, 3, 5])
    assert mask == 21
    assert mask_includes(mask, 3)
    assert not mask_includes(mask, 2)
    with pytest.raises(ValueError, match="between 1 and 7"):
        weekday_mask([0])


def test_lookup_outcomes_ordering_and_disambiguation(tmp_path: Path):
    rows = [
        (date(2025, 6, 15), "WN", "39", "BWI", "ABQ", 500, 700),
        (date(2025, 6, 15), "WN", "39", "GSP", "BWI", 300, 450),
        (date(2025, 6, 16), "DL", "1234", "ATL", "MIA", 1252, 1367),
    ]
    exact, patterns = build(tmp_path, rows)
    multiple = lookup_exact(exact, carrier="wn", flight_date="2025-06-15", flight_number="39")
    assert multiple["classification"] == "multiple_segments"
    assert [row["origin"] for row in multiple["segments"]] == ["GSP", "BWI"]
    assert lookup_exact(
        exact, carrier="WN", flight_date="2025-06-15", flight_number="39", origin="BWI"
    )["classification"] == "unique_match"
    assert lookup_exact(
        exact, carrier="WN", flight_date="2025-06-15", flight_number="39",
        scheduled_departure_minutes=300,
    )["segments"][0]["origin"] == "GSP"
    assert lookup_exact(
        exact, carrier="WN", flight_date="2025-06-15", flight_number="999"
    )["classification"] == "no_match"
    assert lookup_patterns(
        patterns, table="exact_schedule_periods", carrier="WN",
        flight_date="2025-06-15", flight_number="39",
    )["match_count"] == 2


def test_exact_segmentation_reconstructs_weekday_masks_and_respects_gaps(tmp_path: Path):
    monday = date(2025, 1, 6)
    dates = [monday, monday + timedelta(days=2), monday + timedelta(days=7),
             monday + timedelta(days=9), monday + timedelta(days=35)]
    rows = [(day, "DL", "1", "ATL", "BOS", 600, 750) for day in dates]
    exact, patterns = build(tmp_path, rows)
    with duckdb.connect(str(patterns), read_only=True) as connection:
        periods = connection.execute("""
            SELECT valid_from,valid_to,weekday_mask,source_observations
            FROM exact_schedule_periods ORDER BY valid_from
        """).fetchall()
    assert periods == [
        (monday, monday + timedelta(days=9), weekday_mask([1, 3]), 4),
        (monday + timedelta(days=35), monday + timedelta(days=35), weekday_mask([1]), 1),
    ]
    metrics = _accuracy_metrics(exact, patterns)["exact"]
    assert metrics["false_positive_rows"] == 0
    assert metrics["false_negative_rows"] == 0
    assert metrics["source_recall"] == 1.0


def test_pattern_gap_rule_does_not_bridge_seasonal_gap_but_quantifies_short_gap(tmp_path: Path):
    monday = date(2025, 1, 6)
    dates = [monday, monday + timedelta(days=7), monday + timedelta(days=21),
             monday + timedelta(days=70)]
    rows = [(day, "AA", "10", "DFW", "LAX", 480, 600) for day in dates]
    exact, patterns = build(tmp_path, rows)
    with duckdb.connect(str(patterns), read_only=True) as connection:
        periods = connection.execute("""
            SELECT valid_from,valid_to,source_observations
            FROM pattern_schedule_periods ORDER BY valid_from
        """).fetchall()
        exclusions = connection.execute("SELECT count(*) FROM pattern_exclusions").fetchone()[0]
    assert periods == [(monday, monday + timedelta(days=21), 3),
                       (monday + timedelta(days=70), monday + timedelta(days=70), 1)]
    assert exclusions == 1
    metrics = _accuracy_metrics(exact, patterns)["pattern"]
    assert metrics["false_positive_rows"] == 1
    assert metrics["false_negative_rows"] == 0


def test_route_and_schedule_stability_tables(tmp_path: Path):
    rows = [
        (date(2025, 1, 6), "DL", "5", "ATL", "BOS", 600, 750),
        (date(2025, 1, 7), "DL", "5", "ATL", "BOS", 600, 750),
        (date(2025, 1, 8), "DL", "5", "ATL", "JFK", 605, 760),
    ]
    _, patterns = build(tmp_path, rows)
    with duckdb.connect(str(patterns), read_only=True) as connection:
        route = connection.execute("""
            SELECT operating_dates,distinct_routes,dominant_route,route_set_changes
            FROM route_stability
        """).fetchone()
        schedules = connection.execute("""
            SELECT origin,destination,distinct_time_pairs FROM schedule_stability ORDER BY destination
        """).fetchall()
    assert route == (3, 2, "ATL-BOS", 1)
    assert schedules == [("ATL", "BOS", 1), ("ATL", "JFK", 1)]


def test_invalid_lookup_inputs(tmp_path: Path):
    exact, patterns = build(tmp_path, [(date(2025, 1, 1), "DL", "1", "ATL", "BOS", 1, 2)])
    with pytest.raises(ValueError, match="ISO"):
        lookup_exact(exact, carrier="DL", flight_date="not-a-date", flight_number="1")
    with pytest.raises(ValueError, match="three-letter"):
        lookup_exact(exact, carrier="DL", flight_date="2025-01-01", flight_number="1", origin="AT")
    with pytest.raises(ValueError, match="unsupported pattern table"):
        lookup_patterns(
            patterns, table="bad", carrier="DL", flight_date="2025-01-01", flight_number="1"
        )
