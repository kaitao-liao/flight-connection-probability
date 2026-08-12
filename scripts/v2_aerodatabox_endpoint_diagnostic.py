"""One-shot same-process API.Market root/product differential diagnostic."""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import threading
from pathlib import Path
from typing import Any, Callable

import httpx

from backend.flight_connection.aerodatabox_provider import (
    AeroDataBoxFutureFlightProvider,
    transport_exception_diagnostic,
)
from backend.flight_connection.future_flight_provider import FutureFlightProviderError
from scripts.v2_aerodatabox_live_audit import load_local_env

ROOT_URL = "https://prod.api.market/"
FLIGHT_NUMBER = "DL1575"
FLIGHT_DATE = "2026-08-20"
ENVIRONMENT_FLAGS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE",
    "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
)


def _root_check(root_client_factory: Callable[..., Any]) -> dict[str, Any]:
    root: dict[str, Any] = {
        "thread": threading.current_thread().name,
    }
    try:
        with root_client_factory(
            timeout=httpx.Timeout(15.0), follow_redirects=False, trust_env=True,
        ) as client:
            response = client.get(ROOT_URL)
        root.update(
            status="success",
            http_status=response.status_code,
            http_version=response.http_version,
        )
    except httpx.RequestError as error:
        root.update(
            status="failure",
            diagnostic=transport_exception_diagnostic(error, url=ROOT_URL, timeout=15.0),
        )
    return root


def run_root_only(
    *, root_client_factory: Callable[..., Any] = httpx.Client,
) -> dict[str, Any]:
    """Run one credential-free root request without constructing a provider."""
    return {
        "root_check": _root_check(root_client_factory),
        "runtime": {
            "python_executable": sys.executable,
            "python_version": platform.python_version(),
            "httpx_version": httpx.__version__,
            "current_working_directory": str(Path.cwd()),
            "thread_name": threading.current_thread().name,
        },
        "environment_present": {
            name: name in os.environ and bool(os.environ[name]) for name in ENVIRONMENT_FLAGS
        },
    }


def run_diagnostic(
    *,
    root_client_factory: Callable[..., Any] = httpx.Client,
    provider_factory: Callable[[], Any] = AeroDataBoxFutureFlightProvider,
) -> dict[str, Any]:
    root = _root_check(root_client_factory)
    if root["status"] == "failure":
        return {"root_check": root, "flight_lookup": {"status": "not_executed"}}

    provider = provider_factory()
    flight: dict[str, Any] = {
        "flight": FLIGHT_NUMBER,
        "date": FLIGHT_DATE,
        "thread": threading.current_thread().name,
    }
    try:
        result = provider.lookup_by_number(FLIGHT_NUMBER, FLIGHT_DATE)
        flight.update(
            outcome=result.outcome,
            candidate_count=len(result.candidates),
            required_complete=sum(
                all((
                    candidate.marketing_carrier,
                    candidate.marketing_flight_number,
                    candidate.origin_iata,
                    candidate.destination_iata,
                    candidate.scheduled_departure_local,
                    candidate.scheduled_arrival_local,
                ))
                for candidate in result.candidates
            ),
        )
    except FutureFlightProviderError as error:
        flight.update(outcome="provider_error", error_type=type(error).__name__)
        if error.diagnostic is not None:
            flight["diagnostic"] = error.diagnostic
    return {"root_check": root, "flight_lookup": flight}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--root-only", action="store_true")
    args = parser.parse_args()
    if args.root_only:
        print(json.dumps(run_root_only(), indent=2))
        return
    if not args.confirm_live:
        parser.error("--confirm-live is required because one product lookup consumes units")
    load_local_env()
    print(json.dumps(run_diagnostic(), indent=2))


if __name__ == "__main__":
    main()
