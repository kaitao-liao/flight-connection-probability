"""Generate cohort-coverage and year-over-year delay reports."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path

import duckdb

from .delay_model import historical_delay_cohort_counts, historical_delay_distribution

ROUTES = (("DL", "ATL", "JFK"), ("AA", "DFW", "LAX"), ("UA", "ORD", "DEN"))


def analyze(database: Path, *, evaluation_size: int = 500) -> dict:
    coverage = {}
    for carrier, origin, destination in ROUTES:
        key = f"{carrier} {origin}-{destination}"
        coverage[key] = historical_delay_cohort_counts(
            database, carrier=carrier, origin=origin, destination=destination,
            travel_date=date(2025, 8, 20), scheduled_departure_minutes=15 * 60 + 30,
        )

    with duckdb.connect(str(database), read_only=True) as connection:
        sample = connection.execute("""
            SELECT reporting_carrier, origin, destination, flight_date, crs_departure_minutes
            FROM historical_flights
            ORDER BY hash(flight_date, reporting_carrier, origin, destination, crs_departure_minutes)
            LIMIT ?
        """, [evaluation_size]).fetchall()
        yearly_rows = connection.execute("""
            SELECT year, reporting_carrier, origin, destination, count(*) AS sample_size,
                   avg(arrival_delay_minutes) AS mean_minutes,
                   median(arrival_delay_minutes) AS median_minutes,
                   quantile_cont(arrival_delay_minutes, 0.75) AS p75_minutes,
                   quantile_cont(arrival_delay_minutes, 0.90) AS p90_minutes
            FROM historical_flights
            WHERE (reporting_carrier, origin, destination) IN (
                ('DL','ATL','JFK'), ('AA','DFW','LAX'), ('UA','ORD','DEN')
            )
            GROUP BY year, reporting_carrier, origin, destination
            ORDER BY reporting_carrier, origin, destination, year
        """).fetchall()

    fallback = Counter()
    for carrier, origin, destination, flight_date, departure_minutes in sample:
        selected = historical_delay_distribution(
            database, carrier=carrier, origin=origin, destination=destination,
            travel_date=flight_date, scheduled_departure_minutes=departure_minutes,
        )
        fallback[selected.fallback_level] += 1

    yearly_columns = ("year", "carrier", "origin", "destination", "sample_size", "mean_minutes",
                      "median_minutes", "p75_minutes", "p90_minutes")
    return {
        "database": str(database),
        "representative_route_cohort_counts": coverage,
        "fallback_evaluation_size": len(sample),
        "fallback_usage_counts": dict(sorted(fallback.items())),
        "fallback_usage_frequencies": {
            level: round(count / len(sample), 6) for level, count in sorted(fallback.items())
        } if sample else {},
        "year_over_year_route_statistics": [
            dict(zip(yearly_columns, [int(row[0]), *row[1:4], int(row[4]), *map(float, row[5:])]))
            for row in yearly_rows
        ],
    }


def to_markdown(report: dict) -> str:
    lines = ["# Generated historical-data analysis", "", "This file is generated from the local DuckDB dataset.", "",
             "## Representative route cohort counts", ""]
    for route, counts in report["representative_route_cohort_counts"].items():
        lines.append(f"- `{route}`: " + ", ".join(f"{level}={count:,}" for level, count in counts.items()))
    lines.extend(["", "## Fallback usage", ""])
    for level, frequency in report["fallback_usage_frequencies"].items():
        lines.append(f"- `{level}`: {frequency:.1%}")
    lines.extend(["", "## Year-over-year route statistics", "",
                  "| Year | Carrier | Route | N | Mean | Median | P75 | P90 |",
                  "|---:|---|---|---:|---:|---:|---:|---:|"])
    for row in report["year_over_year_route_statistics"]:
        lines.append(
            f"| {row['year']} | {row['carrier']} | {row['origin']}-{row['destination']} | "
            f"{row['sample_size']:,} | {row['mean_minutes']:.2f} | {row['median_minutes']:.2f} | "
            f"{row['p75_minutes']:.2f} | {row['p90_minutes']:.2f} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/processed/flights_full.duckdb"))
    parser.add_argument("--evaluation-size", type=int, default=500)
    parser.add_argument("--json-output", type=Path, default=Path("data/processed/analysis.json"))
    parser.add_argument("--markdown-output", type=Path, default=Path("docs/generated_data_analysis.md"))
    args = parser.parse_args()
    report = analyze(args.database, evaluation_size=args.evaluation_size)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    args.markdown_output.write_text(to_markdown(report), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
