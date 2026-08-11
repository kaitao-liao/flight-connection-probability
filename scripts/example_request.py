"""Submit one example itinerary to a locally running API using the standard library."""
from __future__ import annotations

import argparse
import json
import urllib.request

EXAMPLE = {
    "carrier": "DL",
    "origin": "ATL",
    "connection": "JFK",
    "destination": "BOS",
    "travel_date": "2026-08-20",
    "first_departure_time": "15:30",
    "first_arrival_time": "17:45",
    "connecting_departure_time": "19:10",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/v1/connection-risk")
    args = parser.parse_args()
    request = urllib.request.Request(
        args.url,
        data=json.dumps(EXAMPLE).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        print(json.dumps(json.load(response), indent=2))


if __name__ == "__main__":
    main()
