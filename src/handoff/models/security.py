"""Security metadata for context packages."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ClassificationLevel(str, Enum):
    """Data classification levels."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class SecurityMetadata(BaseModel):
    """Security and privacy metadata for a context package.

    This model enforces the principle of explicit whitelisting over
    blacklisting. Only keys explicitly listed in ``allowed_variable_keys``
    may be included in ``context.state.variables``.
    """

    classification: ClassificationLevel = Field(
        default=ClassificationLevel.INTERNAL,
        description="Data classification level of this package.",
    )
    sanitized: bool = Field(
        default=False,
        description="Whether the package has been through sanitization.",
    )
    allowed_variable_keys: list[str] = Field(
        default_factory=list,
        description="Explicit whitelist of variable keys that may be included.",
    )
    redacted_keys: list[str] = Field(
        default_factory=list,
        description="Keys that were redacted during sanitization (audit trail).",
    )
    encryption_at_rest: bool = Field(
        default=False,
        description="Whether the stored package is encrypted.",
    )
    permission_ttl_seconds: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="Time-to-live for temporarily elevated permissions.",
    )

    def is_key_allowed(self, key: str) -> bool:
        """Check if a variable key is allowed by the whitelist.

        If no whitelist is defined (empty list), all keys are allowed for
        backwards compatibility, but a warning should be emitted.
        """
        if not self.allowed_variable_keys:
            return True
        return key in self.allowed_variable_keys

    def sanitize_variables(self, variables: dict[str, Any]) -> dict[str, Any]:
        """Filter variables according to the whitelist and redaction rules.

        Args:
            variables: Raw state variables from the source agent.

        Returns:
            Sanitized dictionary with only allowed keys.
        """
        sanitized: dict[str, Any] = {}
        redacted: list[str] = []

        for key, value in variables.items():
            if not self.is_key_allowed(key):
                redacted.append(key)
                continue
            # Basic PII pattern detection (extend as needed)
            if isinstance(value, str):
                from handoff.models._pii import scrub_string

                value = scrub_string(value)
            sanitized[key] = value

        self.redacted_keys = list(set(self.redacted_keys + redacted))
        self.sanitized = True
        return sanitized
