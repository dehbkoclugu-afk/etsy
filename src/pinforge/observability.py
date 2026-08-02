from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime


_SECRET_PATTERN = re.compile(
    r"(?i)(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|secret)"
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _redact(record.getMessage()),
        }
        for name in ("correlation_id", "provider", "event_type", "entity_id"):
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = _redact(str(value))
        if record.exc_info:
            payload["exception"] = _redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(*, verbose: bool = False) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("pinforge")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.propagate = False


def _redact(value: str) -> str:
    value = re.sub(
        r"(?i)authorization\s*[:=]\s*Bearer\s+[^\s,}]+",
        "authorization=Bearer [REDACTED]",
        value,
    )
    value = re.sub(
        r"(?i)(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|secret)\s*[:=]\s*[^\s,}]+",
        r"\1=[REDACTED]",
        value,
    )
    return re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/-]+", "Bearer [REDACTED]", value)
