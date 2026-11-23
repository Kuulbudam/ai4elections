"""FastAPI entry point. Phase 0: health endpoint, safe error handling, CORS.

Endpoints from openapi.yaml are added phase by phase using the generated models in
app/contracts/models.py (ADR-0006).
"""

from __future__ import annotations

import logging
import os
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.contracts.models import Error, Health
from app.routes import (
    areas,
    assistant,
    collation,
    dashboard,
    escalations,
    field,
    findings,
    ingestion,
    live,
    reports,
    sheets,
    summary,
)

VERSION = "0.1.0"
log = logging.getLogger("ai4e.api")

app = FastAPI(title="AI4Elections API", version=VERSION, docs_url=None, redoc_url=None)

_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    expose_headers=["X-Report-Content-Hash", "Content-Disposition"],
)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


@app.middleware("http")
async def security_headers(request: Request, call_next):  # noqa: ANN001, ANN201
    """Every API response: no sniffing, no framing, nothing to run; private data is never cached."""
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    if request.url.path != "/health":
        response.headers.setdefault("Cache-Control", "no-store")
    return response


_STATUS_CODES = {
    400: "VALIDATION_FAILED",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "VALIDATION_FAILED",
    409: "CONFLICT",
    422: "VALIDATION_FAILED",
    429: "RATE_LIMITED",
    502: "UPSTREAM_FAILED",
    503: "UNAVAILABLE",
}


def _error(status: int, code: str, message: str, details: list | None = None) -> JSONResponse:
    body = Error(code=code, message=message, request_id=uuid.uuid4().hex, details=details)
    return JSONResponse(status_code=status, content=body.model_dump(mode="json", exclude_none=True))


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _STATUS_CODES.get(exc.status_code, "INTERNAL")
    status = exc.status_code if code != "INTERNAL" else 500
    message = exc.detail if isinstance(exc.detail, str) and (status < 500 or status in (502, 503)) else "Request failed"
    return _error(status, code, message)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"field": ".".join(str(p) for p in e.get("loc", ())), "message": e.get("msg", "invalid")} for e in exc.errors()
    ]
    return _error(400, "VALIDATION_FAILED", "Request is invalid", details)


@app.exception_handler(Exception)
async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error", exc_info=exc)
    return _error(500, "INTERNAL", "An unexpected error occurred")


app.include_router(ingestion.router)
app.include_router(findings.router)
app.include_router(areas.router)
app.include_router(assistant.router)
app.include_router(reports.router)
app.include_router(dashboard.router)
app.include_router(sheets.router)
app.include_router(collation.router)
app.include_router(escalations.router)
app.include_router(field.router)
app.include_router(live.router)
app.include_router(summary.router)


@app.get("/health", response_model=Health)
def health() -> Health:
    return Health(status="ok", version=VERSION)
