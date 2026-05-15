"""Basic PII scrubbing utilities."""

from __future__ import annotations

import re


# Simple regex patterns for common PII - production should use a proper PII scanner
_SENSITIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "<EMAIL>"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "<SSN>"),
    (re.compile(r"sk-[a-zA-Z0-9]{20,}"), "<API_KEY>"),
    (re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b"), "<CREDIT_CARD>"),
]


def scrub_string(text: str) -> str:
    """Scrub sensitive patterns from a string.

    Args:
        text: Input string that may contain PII.

    Returns:
        String with sensitive patterns replaced by placeholders.
    """
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text
