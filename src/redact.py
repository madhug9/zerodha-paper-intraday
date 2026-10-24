"""Strip credential-shaped text before it is logged or sent to the browser."""

from __future__ import annotations

import re

_GOOGLE_KEY = re.compile(r"AIza[0-9A-Za-z_\-]{8,}")
_OPENAI_KEY = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{8,}")
_ASSIGNMENT = re.compile(
    r"(?i)\b[\w.]*(?:api[_-]?key|access[_-]?token|secret|token|password)\b\s*[:=]\s*\S+"
)


def redact(text: str) -> str:
    cleaned = _GOOGLE_KEY.sub("[redacted]", text)
    cleaned = _OPENAI_KEY.sub("[redacted]", cleaned)
    cleaned = _BEARER.sub(r"\1 [redacted]", cleaned)
    cleaned = _ASSIGNMENT.sub("[redacted]", cleaned)
    return cleaned
