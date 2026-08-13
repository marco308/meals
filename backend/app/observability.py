"""Structured logging: the one place that decides what the backend writes to
stdout and how.

Everything is one line per record on stdout — JSON in production so a log
shipper can parse fields without regexes, plain text everywhere else so
`make run` and test failures stay readable (`LOG_FORMAT` overrides either
way). There is deliberately no file handling, rotation or shipping here: the
container runtime owns the files and whatever ships them somewhere is
deployment plumbing, not application code. The app's whole job is to emit
records worth shipping.

Three loggers, so a reader (or a Grafana query) can tell the streams apart:

- ``meals.http``   — one line per request, replacing uvicorn's access log.
- ``meals.events`` — named domain events (registration, deletion, ingest
  outcome…) via :func:`log_event`; the "what happened" log as opposed to the
  per-request one.
- ``meals.error``  — unhandled exceptions, with traceback.

Every request gets an id: an inbound ``X-Request-ID`` is honoured (so a proxy
or client can correlate), otherwise one is minted. The id rides a contextvar,
so a record logged deep in a service carries it without the service knowing;
it is echoed on the response, and quoted in the 500 body so a user report can
be matched to its traceback.

Log fields carry ids — user, household, request — never emails, tokens, or
request bodies. PRIVACY.md promises "ordinary web-server logs"; keep them
ordinary.
"""

import json
import logging
import re
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from app.config import get_settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_access_logger = logging.getLogger("meals.http")
_events_logger = logging.getLogger("meals.events")
_error_logger = logging.getLogger("meals.error")

# Attributes every LogRecord is born with; anything else on a record arrived
# via `extra=` and belongs in the output.
_RECORD_BUILTINS = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in record.__dict__.items() if k not in _RECORD_BUILTINS}


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts/level/logger/msg plus every `extra=` field
    at the top level. The active request id is attached even when the code
    that logged never heard of the request (the mailer, a service helper)."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None) or request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # default=str: UUIDs and datetimes appear in extras; a log line must
        # never be the thing that raises.
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """The same record for humans: message first, `extra=` fields appended as
    key=value, any traceback after — what `make run` and failing tests show."""

    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")

    def format(self, record: logging.LogRecord) -> str:
        fields = _extra_fields(record)
        request_id = getattr(record, "request_id", None) or request_id_var.get()
        if request_id:
            fields.setdefault("request_id", request_id)
        line = super().format(record)
        if fields:
            pairs = " ".join(f"{key}={value}" for key, value in fields.items())
            # Keep a traceback (super() appends it after a newline) last.
            head, newline, tail = line.partition("\n")
            line = f"{head}  {pairs}{newline}{tail}"
        return line


_configured = False


def setup_logging() -> None:
    """Configure the root logger. Called at app import, which is the right
    moment on purpose: uvicorn sets its logging up *before* importing the app,
    so this runs after it and can take over — `uvicorn.access` is silenced
    (the request middleware logs a richer line), and uvicorn's own
    startup/error records propagate to the root handler so every line leaves
    the process in one format."""
    global _configured
    if _configured:
        return
    _configured = True

    settings = get_settings()
    log_format = settings.log_format or ("json" if settings.environment == "production" else "text")
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter() if log_format == "json" else TextFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())

    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False


def log_event(event: str, **fields: Any) -> None:
    """One INFO line on `meals.events` for a named domain event.

    Use for the handful of moments worth finding by name later —
    registrations, account deletions, ingest outcomes — not as a general
    logger. Field values should be ids and enums, never free text from a user.
    """
    _events_logger.info(event, extra=fields)


# An inbound X-Request-ID is only trusted this far: header-safe charset,
# bounded length. Anything else gets a fresh id rather than a 400 — the id is
# a courtesy, not a contract.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


async def request_logging_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """One log line per request, and a request id on everything.

    Also the last-resort exception handler: an unhandled error is logged with
    its traceback and answered with a 500 whose detail quotes the request id,
    instead of propagating to uvicorn where the response would be an opaque
    connection reset and the traceback would carry no request context.
    """
    inbound = request.headers.get("X-Request-ID", "")
    request_id = inbound if _REQUEST_ID_RE.match(inbound) else uuid.uuid4().hex[:16]
    token = request_id_var.set(request_id)
    start = time.perf_counter()
    try:
        try:
            response: Response = await call_next(request)
        except Exception:
            _error_logger.exception(
                "unhandled error on %s %s",
                request.method,
                request.url.path,
                extra={"request_id": request_id, "method": request.method, "path": request.url.path},
            )
            response = JSONResponse(
                status_code=500,
                content={
                    "detail": (
                        "the server hit an unexpected error handling this request — not something you did. "
                        f"Try again, and if it keeps happening report it quoting request id {request_id}."
                    )
                },
            )
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        response.headers["X-Request-ID"] = request_id

        # The swarm healthcheck (5s) and Traefik's LB check (30s) poll
        # /healthz forever; logging their 200s would be ~20k identical lines a
        # day drowning everything else. A *failing* healthz is always logged.
        path = request.url.path
        if path == "/healthz" and response.status_code < 400:
            return response

        fields: dict[str, Any] = {
            "request_id": request_id,
            "method": request.method,
            "path": path,
            # The matched route template ("/recipes/{recipe_id}") so lines
            # group by endpoint; falls back to the raw path when nothing
            # matched (404s, gate rejections).
            "route": getattr(request.scope.get("route"), "path", path),
            "status": response.status_code,
            "duration_ms": duration_ms,
        }
        client = getattr(request.state, "client", None)
        if client is not None:
            fields["client_platform"] = client.platform
            fields["client_version"] = client.version
            fields["client_build"] = client.build
        for attr in ("user_id", "household_id"):
            value = getattr(request.state, attr, None)
            if value is not None:
                fields[attr] = value
        level = logging.ERROR if response.status_code >= 500 else logging.INFO
        _access_logger.log(
            level, "%s %s -> %s in %sms", request.method, path, response.status_code, duration_ms, extra=fields
        )
        return response
    finally:
        request_id_var.reset(token)
