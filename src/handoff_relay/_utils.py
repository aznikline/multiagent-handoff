"""Shared utilities for handoff-relay."""

from __future__ import annotations


# Maps legacy/alias reason values to valid HandoffReason enum values.
# Keep in sync with HandoffReason enum in handoff.models.task.
_REASON_ALIASES: dict[str, str] = {
    "manual": "user_triggered",
    "rate_limit": "user_triggered",
    "error": "error_recovery",
}


def normalize_reason(reason: str) -> str:
    """Map legacy/alias reason values to valid HandoffReason enum values.

    Args:
        reason: Raw reason string from user input or agent command.

    Returns:
        Normalized reason string valid for HandoffReason.
    """
    return _REASON_ALIASES.get(reason, reason)
