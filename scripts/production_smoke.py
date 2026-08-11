"""Lightweight smoke checks for deployed frontend, API, and production CORS."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import urllib.error
import urllib.request


def request(url: str, *, method: str = "GET", body: bytes | None = None, headers=None):
    return urllib.request.urlopen(
        urllib.request.Request(url, data=body, headers=headers or {}, method=method),
        timeout=60,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", required=True, help="API origin, for example https://api.example.com")
    parser.add_argument("--frontend", required=True, help="Public frontend HTTPS URL")
    parser.add_argument("--payload", type=Path, default=Path("examples/connection_request.json"))
    args = parser.parse_args()
    api = args.api.rstrip("/")
    frontend = args.frontend.rstrip("/")

    with request(frontend) as response:
        assert response.status == 200, f"frontend returned {response.status}"
    with request(f"{api}/health") as response:
        health = json.load(response)
        assert response.status == 200 and health == {"status": "ok"}, health

    origin = frontend
    with request(
        f"{api}/api/v1/connection-risk",
        method="OPTIONS",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    ) as response:
        assert response.headers.get("Access-Control-Allow-Origin") == origin, response.headers

    payload = args.payload.read_bytes()
    with request(
        f"{api}/api/v1/connection-risk",
        method="POST",
        body=payload,
        headers={"Content-Type": "application/json", "Origin": origin},
    ) as response:
        result = json.load(response)
        probability = result.get("connection_probability")
        assert response.status == 200 and isinstance(probability, (int, float))
        assert 0 <= probability <= 1, result
        assert "risk_level" not in result

    print(json.dumps({
        "frontend_http_200": True,
        "backend_health_ok": True,
        "cors_origin": origin,
        "connection_probability": probability,
        "model_version": result["model"]["version"],
    }, indent=2))


if __name__ == "__main__":
    main()
