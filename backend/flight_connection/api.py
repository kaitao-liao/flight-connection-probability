"""FastAPI transport layer."""
from __future__ import annotations

import os
from pathlib import Path
from contextlib import asynccontextmanager
import logging
import importlib
import threading
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from .schemas import ConnectionRiskRequest, ConnectionRiskResponse
from .service import ConnectionRiskService

LOGGER = logging.getLogger("flight_connection.api")


class _LazyFutureFlightProvider:
    """Construct the credential-bearing provider only when a V2 lookup is requested."""

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._provider: Any | None = None
        self._lock = threading.Lock()

    def lookup_by_number(self, flight_number, date_local):
        if self._provider is None:
            with self._lock:
                if self._provider is None:
                    self._provider = self._factory()
        return self._provider.lookup_by_number(flight_number, date_local)


def _production_v2_provider_factory() -> Any:
    module = importlib.import_module(".aerodatabox_provider", __package__)
    return module.AeroDataBoxFutureFlightProvider()


def _environment() -> str:
    return os.getenv("FLIGHT_CONNECTION_ENV", "development").strip().lower()


def _database_path(database: str | Path | None) -> str | Path:
    if database is not None:
        return database
    configured = os.getenv("FLIGHT_CONNECTION_DB")
    if configured:
        return configured
    if _environment() == "production":
        raise RuntimeError("FLIGHT_CONNECTION_DB is required in production")
    return "data/processed/flights_development.duckdb"


def _cors_origins(allowed_origins: list[str] | None) -> list[str]:
    if allowed_origins is not None:
        origins = allowed_origins
    else:
        configured = os.getenv("FLIGHT_CONNECTION_CORS_ORIGINS")
        if configured:
            origins = [origin.strip() for origin in configured.split(",") if origin.strip()]
        elif _environment() == "production":
            raise RuntimeError("FLIGHT_CONNECTION_CORS_ORIGINS is required in production")
        else:
            origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
    if not origins:
        raise RuntimeError("at least one CORS origin must be configured")
    if "*" in origins:
        raise RuntimeError("wildcard CORS origins are not allowed")
    return origins


def create_app(
    database: str | Path | None = None,
    *,
    service: ConnectionRiskService | None = None,
    v2_service: Any | None = None,
    v2_provider_factory: Callable[[], Any] | None = None,
    allowed_origins: list[str] | None = None,
) -> FastAPI:
    risk_service = service or ConnectionRiskService(_database_path(database))
    if v2_service is not None and v2_provider_factory is not None:
        raise ValueError("provide either v2_service or v2_provider_factory, not both")
    itinerary_service = v2_service
    if itinerary_service is None and v2_provider_factory is not None:
        module = importlib.import_module(".v2_itinerary_service", __package__)
        itinerary_service = module.V2ItineraryService(
            _LazyFutureFlightProvider(v2_provider_factory), risk_service,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        risk_service.validate_database()
        LOGGER.info(
            "Flight Connection Probability API ready; database=%s environment=%s",
            risk_service.database,
            _environment(),
        )
        yield

    app = FastAPI(
        title="Flight Connection Probability API",
        version="1.0.0",
        lifespan=lifespan,
    )
    origins = _cors_origins(allowed_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["POST"],
        allow_headers=["Content-Type"],
    )
    app.state.connection_risk_service = risk_service
    app.state.v2_itinerary_service = itinerary_service

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/v1/connection-risk", response_model=ConnectionRiskResponse)
    def connection_risk(payload: ConnectionRiskRequest, request: Request) -> ConnectionRiskResponse:
        try:
            return request.app.state.connection_risk_service.estimate(payload)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except (OSError, RuntimeError) as error:
            raise HTTPException(status_code=503, detail="historical flight data is unavailable") from error

    if itinerary_service is not None:
        schemas = importlib.import_module(".v2_schemas", __package__)
        # FastAPI resolves postponed annotations against module globals. This binding is
        # created only for an explicitly V2-enabled application.
        globals()["_v2_schemas_runtime"] = schemas

        @app.post("/api/v2/connection-risk", response_model=schemas.V2ConnectionResponse)
        def v2_connection_risk(
            payload: _v2_schemas_runtime.V2ConnectionRequest, request: Request,
        ) -> _v2_schemas_runtime.V2ConnectionResponse:
            return request.app.state.v2_itinerary_service.estimate(payload)

    return app


def create_default_app() -> FastAPI:
    """Create the environment-selected serving app without constructing a provider."""
    provider_factory = _production_v2_provider_factory if _environment() == "production" else None
    return create_app(v2_provider_factory=provider_factory)


app = create_default_app()
