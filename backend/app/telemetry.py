import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog
from prometheus_client import Counter, Histogram

DISCOVERY = Counter("arb_discovery_total", "Discovery runs", ["venue", "result"])
BOOKS = Counter("arb_book_updates_total", "Normalized book updates", ["venue"])
FAILURES = Counter("arb_failures_total", "Sanitized operational failures", ["role", "code"])
DECISIONS = Counter("arb_decisions_total", "Opportunity decisions", ["result"])
LATENCY = Histogram("arb_http_seconds", "HTTP response latency")


def redact(_: Any, __: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key in list(event):
        if any(
            term in key.lower()
            for term in ("password", "secret", "signature", "authorization", "key")
        ):
            event[key] = "[REDACTED]"
    return event


def configure_logging() -> None:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
    for noisy in ("httpx", "httpcore", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            redact,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
