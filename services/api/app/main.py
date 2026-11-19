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
from app.routes import findings, ingestion

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
)

_STATUS_CODES = {
    400: "VALIDATION_FAILED",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "VALIDATION_FAILED",
    409: "CONFLICT",
    422: "VALIDATION_FAILED",
    429: "RATE_LIMITED",
}


def _error(status: int, code: str, message: str, details: list | None = None) -> JSONResponse:
    body = Error(code=code, message=message, request_id=uuid.uuid4().hex, details=details)
    return JSONResponse(status_code=status, content=body.model_dump(mode="json", exclude_none=True))


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _STATUS_CODES.get(exc.status_code, "INTERNAL")
    status = exc.status_code if code != "INTERNAL" else 500
    message = exc.detail if isinstance(exc.detail, str) and status < 500 else "Request failed"
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


@app.get("/health", response_model=Health)
def health() -> Health:
    return Health(status="ok", version=VERSION)
