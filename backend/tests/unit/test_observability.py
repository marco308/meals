"""The two log formatters (app/observability.py). Integration tests assert on
log *records*; these assert on the rendered lines, which is what an operator
(or a log shipper) actually gets."""

import json
import logging
import sys
import uuid

from app.observability import JsonFormatter, TextFormatter, request_id_var


def make_record(msg: str = "hello %s", args: tuple = ("world",), **extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="meals.test", level=logging.INFO, pathname=__file__, lineno=1, msg=msg, args=args, exc_info=None
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_emits_one_parseable_object():
    line = JsonFormatter().format(make_record(status=200, duration_ms=3.2))
    payload = json.loads(line)
    assert payload["msg"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "meals.test"
    assert payload["status"] == 200
    assert payload["duration_ms"] == 3.2
    # ISO-8601 with explicit UTC offset, so shippers never guess the zone.
    assert payload["ts"].endswith("+00:00")


def test_json_formatter_survives_unserialisable_extras():
    # UUIDs ride on access-log records; a log line must never be what raises.
    user_id = uuid.uuid4()
    payload = json.loads(JsonFormatter().format(make_record(user_id=user_id)))
    assert payload["user_id"] == str(user_id)


def test_json_formatter_attaches_ambient_request_id():
    token = request_id_var.set("req-ambient-1")
    try:
        payload = json.loads(JsonFormatter().format(make_record()))
    finally:
        request_id_var.reset(token)
    assert payload["request_id"] == "req-ambient-1"


def test_json_formatter_includes_traceback():
    try:
        raise ValueError("kaboom")
    except ValueError:
        record = make_record()
        record.exc_info = sys.exc_info()
    payload = json.loads(JsonFormatter().format(record))
    assert "ValueError: kaboom" in payload["exc"]
    assert "Traceback" in payload["exc"]


def test_text_formatter_appends_fields_before_any_traceback():
    try:
        raise ValueError("kaboom")
    except ValueError:
        record = make_record(status=500)
        record.exc_info = sys.exc_info()
    line = TextFormatter().format(record)
    first_line = line.splitlines()[0]
    assert "hello world" in first_line
    assert "status=500" in first_line
    assert "ValueError: kaboom" in line
