"""FastAPI transport layer."""
from __future__ import annotations

import os
from pathlib import Path
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from .schemas import ConnectionRiskRequest, ConnectionRiskResponse
from .service import ConnectionRiskService

LOGGER = logging.getLogger("flight_connection.api")


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
    allowed_origins: list[str] | None = None,
) -> FastAPI:
    risk_service = service or ConnectionRiskService(_database_path(database))

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

    return app


app = create_app()
