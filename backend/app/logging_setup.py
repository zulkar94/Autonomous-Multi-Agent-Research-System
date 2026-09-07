"""Structured JSON logging with request correlation IDs and secret redaction."""

from __future__ import annotations

import logging
import re
import sys
from contextvars import ContextVar

from pythonjsonlogger import json as jsonlogger

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
user_id_var: ContextVar[str] = ContextVar("user_id", default="-")

_SECRET_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9\-_]{8,})"),
    re.compile(r"(Bearer\s+)[A-Za-z0-9\-_.]+", re.IGNORECASE),
    re.compile(r"((?:api[_-]?key|password|secret|token)[\"'\s:=]+)([^\s\"',}]+)", re.IGNORECASE),
]


def redact(text: str) -> str:
    """Strip credential-looking substrings before they reach a log sink."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) if m.lastindex else "") + "[REDACTED]", text)
    return text


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.user_id = user_id_var.get()
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if json_output:
        handler.setFormatter(
            jsonlogger.JsonFormatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s %(user_id)s",
                rename_fields={"asctime": "ts", "levelname": "level", "name": "logger"},
            )
        )
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s")
        )
    handler.addFilter(ContextFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
