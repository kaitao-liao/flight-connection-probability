"""Research-only AeroDataBox adapter; isolated from all V1 serving code."""
from __future__ import annotations

import errno
import json
import os
import re
import socket
import ssl
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit

import httpx

from .future_flight_provider import (
    FutureFlightCandidate,
    FutureFlightLookupResult,
    MissingProviderCredential,
    MissingRequiredScheduleFields,
    ProviderAuthenticationError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderNormalizationError,
    ProviderResponseError,
    ProviderTimeoutError,
)

AERODATABOX_SOURCE = "aerodatabox_api_market"
AERODATABOX_BASE_URL = "https://prod.api.market/api/v1/aedbx/aerodatabox"
AUTH_HEADER = "x-api-market-key"
_FLIGHT_NUMBER = re.compile(r"^[A-Z0-9]{2,3}\s?\d{1,4}[A-Z]?$", re.ASCII)


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes = b""
    headers: Mapping[str, str] | None = None
    reason_phrase: str | None = None


class HttpTransport(Protocol):
    def get(
        self, url: str, *, params: Mapping[str, str], headers: Mapping[str, str], timeout: float
    ) -> HttpResponse: ...


class HttpxTransport:
    """One-shot synchronous transport with no retries, logging, or redirects."""

    def __init__(self, *, client_factory: Callable[..., Any] = httpx.Client) -> None:
        self._client_factory = client_factory

    def get(
        self, url: str, *, params: Mapping[str, str], headers: Mapping[str, str], timeout: float
    ) -> HttpResponse:
        try:
            with self._client_factory(
                follow_redirects=False,
                timeout=httpx.Timeout(timeout),
                trust_env=True,
            ) as client:
                response = client.get(url, params=params, headers=headers)
            return HttpResponse(
                response.status_code, response.content, dict(response.headers),
                response.reason_phrase,
            )
        except httpx.TimeoutException as error:
            raise ProviderTimeoutError(
                "Future-flight provider request timed out",
                diagnostic=transport_exception_diagnostic(error, url=url, timeout=timeout),
            ) from None
        except httpx.RequestError as error:
            raise ProviderResponseError(
                "Future-flight provider network request failed",
                diagnostic=transport_exception_diagnostic(error, url=url, timeout=timeout),
            ) from None


def transport_exception_diagnostic(
    error: httpx.RequestError, *, url: str, timeout: float,
) -> dict[str, Any]:
    """Classify a pre-response failure without serializing exception/request content."""
    category, phase = _transport_error_category(error)
    return {
        "transport_error_category": category,
        "exception_class": type(error).__name__,
        "phase": phase,
        "response_received": False,
        "host": urlsplit(url).hostname,
        "request_method": "GET",
        "timeout_seconds": timeout,
        "trust_env": True,
        "follow_redirects": False,
        "thread_name": threading.current_thread().name,
    }


def _transport_error_category(error: httpx.RequestError) -> tuple[str, str]:
    timeout_categories = {
        httpx.ConnectTimeout: ("connect_timeout", "connect"),
        httpx.ReadTimeout: ("read_timeout", "read"),
        httpx.WriteTimeout: ("write_timeout", "write"),
        httpx.PoolTimeout: ("pool_timeout", "pool"),
    }
    for error_type, result in timeout_categories.items():
        if isinstance(error, error_type):
            return result
    if isinstance(error, httpx.ConnectError):
        return _connect_error_category(error), "connect"
    categories = (
        (httpx.ReadError, "read_error", "read"),
        (httpx.WriteError, "write_error", "write"),
        (httpx.CloseError, "close_error", "network"),
        (httpx.ProxyError, "proxy_error", "proxy"),
        (httpx.RemoteProtocolError, "remote_protocol_error", "protocol"),
        (httpx.LocalProtocolError, "local_protocol_error", "protocol"),
        (httpx.NetworkError, "network_error", "network"),
        (httpx.TransportError, "transport_error", "unknown"),
        (httpx.RequestError, "request_error", "unknown"),
    )
    return next(
        ((category, phase) for error_type, category, phase in categories
         if isinstance(error, error_type)),
        ("unknown_httpx_error", "unknown"),
    )


def _connect_error_category(error: httpx.ConnectError) -> str:
    """Use only structured cause types/errno; never inspect arbitrary messages."""
    for cause in _exception_chain(error):
        if isinstance(cause, ssl.SSLError):
            return "tls_error"
        if isinstance(cause, socket.gaierror):
            return "dns_error"
        if isinstance(cause, OSError):
            if cause.errno in {errno.ECONNREFUSED, 10061}:
                return "connection_refused"
            if cause.errno in {errno.ECONNRESET, 10054}:
                return "connection_reset"
    return "connect_error_unknown"


def _exception_chain(error: BaseException):
    seen: set[int] = set()
    current = error.__cause__ or error.__context__
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def api_key_from_environment() -> str:
    key = os.environ.get("AERODATABOX_API_KEY", "").strip()
    if not key:
        raise MissingProviderCredential("AERODATABOX_API_KEY is required")
    return key


def redact_secrets(value: Any, *, secrets: tuple[str, ...] = ()) -> Any:
    """Recursively redact credentials and authorization-like header values."""
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if str(key).lower() in {AUTH_HEADER, "authorization", "x-magicapi-key"}
                else redact_secrets(item, secrets=secrets)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item, secrets=secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(item, secrets=secrets) for item in value)
    if isinstance(value, str):
        redacted = value
        for secret in secrets:
            if secret:
                redacted = redacted.replace(secret, "[REDACTED]")
        return redacted
    return value


class AeroDataBoxFutureFlightProvider:
    def __init__(
        self, *, api_key: str | None = None, transport: HttpTransport | None = None,
        timeout_seconds: float = 15.0, now_utc: Callable[[], datetime] | None = None,
    ) -> None:
        self._api_key = api_key.strip() if api_key is not None else api_key_from_environment()
        if not self._api_key:
            raise MissingProviderCredential("AERODATABOX_API_KEY is required")
        self._transport = transport or HttpxTransport()
        self._timeout = timeout_seconds
        self._now_utc = now_utc or (lambda: datetime.now(timezone.utc))

    def lookup_by_number(
        self, flight_number: str, date_local: date | str
    ) -> FutureFlightLookupResult:
        normalized_number = flight_number.replace(" ", "").upper()
        if not _FLIGHT_NUMBER.fullmatch(normalized_number):
            raise ValueError("flight_number must contain a carrier code and numeric flight number")
        normalized_date = date.fromisoformat(str(date_local))
        response = self._transport.get(
            f"{AERODATABOX_BASE_URL}/flights/Number/{normalized_number}/{normalized_date.isoformat()}",
            params={
                "dateLocalRole": "Departure", "withAircraftImage": "false",
                "withLocation": "false", "withFlightPlan": "false",
            },
            headers={AUTH_HEADER: self._api_key, "Accept": "application/json"},
            timeout=self._timeout,
        )
        if response.status_code == 204:
            return FutureFlightLookupResult("no_match", ())
        self._raise_for_response(response)
        try:
            payload = json.loads(response.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ProviderNormalizationError(
                "Future-flight provider returned malformed JSON"
            ) from error
        if not isinstance(payload, list):
            raise ProviderNormalizationError(
                "Future-flight provider response must be an array"
            )
        retrieved_at = self._now_utc()
        normalized_candidates = []
        for index, item in enumerate(payload):
            try:
                normalized_candidates.append(
                    self._normalize(item, normalized_number, normalized_date, retrieved_at)
                )
            except ProviderResponseError:
                raise
            except (KeyError, TypeError, ValueError) as error:
                raise ProviderNormalizationError(
                    f"Future-flight provider candidate {index} could not be normalized"
                ) from error
        candidates = tuple(sorted(normalized_candidates, key=lambda item: (
            item.scheduled_departure_local, item.origin_iata, item.destination_iata,
            item.marketing_flight_number,
        )))
        outcome = "no_match" if not candidates else "unique_match" if len(candidates) == 1 else "multiple_candidates"
        return FutureFlightLookupResult(outcome, candidates)

    @staticmethod
    def _raise_for_response(response: HttpResponse) -> None:
        status = response.status_code
        if 200 <= status < 300:
            # Usage/quota headers are informational on successful responses. They must
            # never override the HTTP success status or trigger provider errors.
            return
        diagnostic = sanitized_response_diagnostic(response)
        if status == 401:
            raise ProviderAuthenticationError(
                "Future-flight provider authentication failed", diagnostic=diagnostic,
            )
        if status == 402 or (status == 403 and _has_explicit_quota_error(response.body)):
            raise ProviderQuotaError(
                "Future-flight provider quota or subscription is exhausted",
                diagnostic=diagnostic,
            )
        if status == 429:
            raise ProviderRateLimitError(
                "Future-flight provider rate limit was reached", diagnostic=diagnostic,
            )
        if status in {408, 504}:
            raise ProviderTimeoutError(
                "Future-flight provider request timed out", diagnostic=diagnostic,
            )
        raise ProviderResponseError(
            f"Future-flight provider returned HTTP {status}", diagnostic=diagnostic,
        )

    @staticmethod
    def _normalize(
        item: Any, requested_number: str, requested_date: date, retrieved_at: datetime,
    ) -> FutureFlightCandidate:
        if not isinstance(item, Mapping):
            raise ProviderNormalizationError(
                "Future-flight provider array contains a non-object"
            )
        departure = _mapping(item.get("departure"))
        arrival = _mapping(item.get("arrival"))
        departure_airport = _mapping(departure.get("airport"))
        arrival_airport = _mapping(arrival.get("airport"))
        departure_time = _local_datetime(departure.get("scheduledTime"))
        arrival_time = _local_datetime(arrival.get("scheduledTime"))
        number = _text(item.get("number")) or requested_number
        marketing_carrier = _carrier_from_number(number)
        airline = _mapping(item.get("airline"))
        marketing_carrier = _text(airline.get("iata")) or marketing_carrier
        origin = _text(departure_airport.get("iata"))
        destination = _text(arrival_airport.get("iata"))
        if not all((marketing_carrier, number, origin, destination, departure_time, arrival_time)):
            raise MissingRequiredScheduleFields(
                "Future-flight provider candidate is missing required schedule fields"
            )
        codeshare_status = _text(item.get("codeshareStatus"))
        operating_carrier = marketing_carrier if codeshare_status == "IsOperator" else None
        operating_number = number if codeshare_status == "IsOperator" else None
        aircraft = _mapping(item.get("aircraft"))
        model = _text(aircraft.get("model"))
        quality = tuple(
            [f"departure:{value}" for value in _string_list(departure.get("quality"))]
            + [f"arrival:{value}" for value in _string_list(arrival.get("quality"))]
        )
        return FutureFlightCandidate(
            source=AERODATABOX_SOURCE, retrieved_at_utc=retrieved_at,
            flight_date=departure_time.date() if departure_time else requested_date,
            marketing_carrier=marketing_carrier.upper(), marketing_flight_number=number.upper(),
            operating_carrier=operating_carrier.upper() if operating_carrier else None,
            operating_flight_number=operating_number.upper() if operating_number else None,
            origin_iata=origin.upper(), destination_iata=destination.upper(),
            scheduled_departure_local=departure_time, scheduled_arrival_local=arrival_time,
            departure_terminal=_text(departure.get("terminal")),
            arrival_terminal=_text(arrival.get("terminal")),
            departure_gate=_text(departure.get("gate")), arrival_gate=_text(arrival.get("gate")),
            status=_text(item.get("status")), quality=quality, codeshare_status=codeshare_status,
            aircraft_type=model, raw_provider_id=_text(item.get("callSign")),
        )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _local_datetime(value: Any) -> datetime | None:
    local = _text(_mapping(value).get("local"))
    if not local:
        return None
    try:
        return datetime.fromisoformat(local.replace("Z", "+00:00"))
    except ValueError as error:
        raise ProviderNormalizationError(
            "Provider supplied an invalid scheduled local time"
        ) from error


def _carrier_from_number(number: str) -> str | None:
    match = re.match(r"^([A-Z0-9]{2,3})\s?\d", number.upper())
    return match.group(1) if match else None


def _string_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _has_explicit_quota_error(body: bytes) -> bool:
    """Recognize machine-readable quota evidence without echoing provider content."""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    if not isinstance(payload, Mapping):
        return False
    known_codes = {
        "insufficient_quota", "quota_exceeded", "subscription_exhausted",
        "subscription_required", "usage_limit_exceeded",
    }
    values = (payload.get("code"), payload.get("error"), payload.get("type"))
    return any(isinstance(value, str) and value.lower() in known_codes for value in values)


_SENSITIVE_HEADER_PARTS = ("authorization", "api-key", "key", "token", "cookie", "secret")
_REQUEST_ID_HEADERS = (
    "x-request-id", "request-id", "x-correlation-id", "cf-ray", "x-amzn-requestid",
    "x-api-request-id",
)
_SAFE_JSON_CODE_FIELDS = ("code", "error", "type")
_SAFE_JSON_MESSAGE_FIELDS = ("message", "error_description", "detail")


def sanitized_response_diagnostic(response: HttpResponse) -> dict[str, Any]:
    """Return bounded response metadata without request data or arbitrary body content."""
    headers = {
        str(name).lower(): str(value)
        for name, value in (response.headers or {}).items()
        if not _is_sensitive_header_name(str(name))
    }
    content_type = _safe_metadata_value(headers.get("content-type"))
    body_kind, payload = _body_kind(response.body, content_type)
    diagnostic: dict[str, Any] = {
        "status": response.status_code,
        "reason_phrase": _safe_metadata_value(response.reason_phrase),
        "content_type": content_type,
        "server": _safe_metadata_value(headers.get("server")),
        "request_ids": {
            name: value
            for name in _REQUEST_ID_HEADERS
            if (value := _safe_metadata_value(headers.get(name))) is not None
        },
        "header_names": sorted(headers),
        "body_kind": body_kind,
        "body_length": len(response.body),
    }
    if body_kind == "json" and isinstance(payload, Mapping):
        diagnostic["json_top_level_keys"] = sorted(
            str(key) for key in payload if not _is_sensitive_header_name(str(key))
        )
        code = _first_safe_json_code(payload)
        message = _first_safe_json_message(payload)
        if code is not None:
            diagnostic["error_code"] = code
        if message is not None:
            diagnostic["message"] = message
    elif body_kind in {"html", "text"}:
        diagnostic["body_marker"] = _body_marker(response.body)
    return diagnostic


def _is_sensitive_header_name(name: str) -> bool:
    lowered = name.lower()
    return any(part in lowered for part in _SENSITIVE_HEADER_PARTS)


def _safe_metadata_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())[:200]
    if not normalized or _looks_secret_bearing(normalized):
        return None
    return normalized


def _body_kind(body: bytes, content_type: str | None) -> tuple[str, Any]:
    if not body:
        return "empty", None
    lowered_type = (content_type or "").lower()
    try:
        decoded = body.decode("utf-8")
    except UnicodeDecodeError:
        return "unknown", None
    stripped = decoded.lstrip().lower()
    if "json" in lowered_type or stripped.startswith(("{", "[")):
        try:
            return "json", json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return "text" if decoded else "unknown", None
    if "html" in lowered_type or stripped.startswith(("<!doctype html", "<html")):
        return "html", None
    if lowered_type.startswith("text/") or all(
        character.isprintable() or character.isspace() for character in decoded
    ):
        return "text", None
    return "unknown", None


def _first_safe_json_code(payload: Mapping[str, Any]) -> str | None:
    for field in _SAFE_JSON_CODE_FIELDS:
        value = payload.get(field)
        if (
            isinstance(value, str)
            and re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", value)
            and not _looks_secret_bearing(value)
        ):
            return value
    return None


def _first_safe_json_message(payload: Mapping[str, Any]) -> str | None:
    for field in _SAFE_JSON_MESSAGE_FIELDS:
        value = payload.get(field)
        if isinstance(value, str):
            normalized = " ".join(value.split())[:160]
            return "[REDACTED]" if _looks_secret_bearing(normalized) else normalized or None
    return None


def _looks_secret_bearing(value: str) -> bool:
    lowered = value.lower()
    return (
        any(part in lowered for part in _SENSITIVE_HEADER_PARTS)
        or re.search(r"(?:bearer\s+|sk[-_])[A-Za-z0-9._~+/=-]+", value, re.IGNORECASE)
        is not None
        or re.search(r"[A-Za-z0-9._~+/=-]{24,}", value) is not None
    )


def _body_marker(body: bytes) -> str:
    lowered = body.decode("utf-8", errors="ignore").lower()
    markers = (
        ("cloudflare", "cloudflare"), ("api.market", "api.market"),
        ("access denied", "access_denied"), ("forbidden", "forbidden"),
    )
    return next((label for needle, label in markers if needle in lowered), "unknown")
