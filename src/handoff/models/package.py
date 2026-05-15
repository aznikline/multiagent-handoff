"""Core ContextPackage model and versioning."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from handoff._utils import utc_now

from pydantic import BaseModel, Field

from handoff.models.context import AgentState, ConversationState, MemorySnapshot
from handoff.models.security import SecurityMetadata
from handoff.models.task import HandoffReason, Priority, TaskInfo

SPEC_VERSION = "1.0"


class AgentFramework(str, Enum):
    """Supported agent frameworks."""

    LANGGRAPH = "langgraph"
    CREWAI = "crewai"
    OPENAI = "openai"
    AUTOGEN = "autogen"
    CUSTOM = "custom"


class SemanticVersion(BaseModel):
    """Structured semantic version for framework compatibility."""

    major: int = Field(ge=0)
    minor: int = Field(ge=0)
    patch: int = Field(ge=0)

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    @classmethod
    def parse(cls, version_str: str) -> SemanticVersion:
        """Parse a semantic version string.

        Args:
            version_str: String in "major.minor.patch" format.

        Returns:
            Parsed SemanticVersion.

        Raises:
            ValueError: If the string is not a valid semantic version.
        """
        parts = version_str.split(".")
        if len(parts) != 3:
            raise ValueError(f"Invalid semantic version: {version_str}")
        return cls(major=int(parts[0]), minor=int(parts[1]), patch=int(parts[2]))


class SourceInfo(BaseModel):
    """Information about the source agent."""

    agent_id: str
    agent_role: str = ""
    framework: AgentFramework = Field(default=AgentFramework.CUSTOM)
    version: SemanticVersion = Field(default_factory=lambda: SemanticVersion.parse("1.0.0"))


class TruncationMeta(BaseModel):
    """Metadata about conversation truncation (moved from conversation to meta layer per review)."""

    applied: bool = False
    strategy: str = "none"
    truncated_message_count: int = 0
    summary_prefix: str = ""


class CompatibilityMeta(BaseModel):
    """Schema compatibility information."""

    source_schema_version: str = SPEC_VERSION
    min_compatible_version: str = SPEC_VERSION
    migration_hints: dict[str, Any] = Field(default_factory=dict)


class PackageMeta(BaseModel):
    """Metadata for a context package."""

    spec_version: str = SPEC_VERSION
    package_id: str = Field(default_factory=lambda: str(uuid4()))
    trace_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Distributed trace/correlation ID for observability.",
    )
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime | None = Field(
        default=None,
        description="TTL for automatic cleanup. If None, no automatic expiry.",
    )
    source: SourceInfo
    handoff_reason: HandoffReason
    priority: Priority = Priority.NORMAL
    truncation: TruncationMeta = Field(default_factory=TruncationMeta)


class ContextBody(BaseModel):
    """Grouping of context-related fields."""

    conversation: ConversationState = Field(default_factory=ConversationState)
    state: AgentState = Field(default_factory=AgentState)
    memory: MemorySnapshot = Field(default_factory=MemorySnapshot)


class ContextPackage(BaseModel):
    """Standardized context package for agent handoff.

    This is the core data structure that carries all state needed for one
    agent to resume another agent's work.
    """

    meta: PackageMeta
    task: TaskInfo
    context: ContextBody = Field(default_factory=ContextBody)
    security: SecurityMetadata = Field(default_factory=SecurityMetadata)
    compatibility: CompatibilityMeta = Field(default_factory=CompatibilityMeta)

    def is_expired(self) -> bool:
        """Check if this package has passed its expiry time."""
        if self.meta.expires_at is None:
            return False
        return utc_now() > self.meta.expires_at

    def validate_security(self) -> list[str]:
        """Validate that state variables comply with security whitelist.

        Returns:
            List of validation error messages (empty if valid).
        """
        errors: list[str] = []
        variables = self.context.state.variables

        if not self.security.allowed_variable_keys:
            # Backwards compatible but warn
            return ["warning: no variable whitelist defined; all keys allowed"]

        for key in variables:
            if not self.security.is_key_allowed(key):
                errors.append(f"security: variable key '{key}' not in whitelist")

        return errors

    def sanitize(self) -> ContextPackage:
        """Return a sanitized copy with disallowed variables removed.

        Returns:
            New ContextPackage with sanitized state variables.
        """
        # Create a copy to avoid mutating self (immutability principle)
        package_copy = self.model_copy(deep=True)
        sanitized_vars = package_copy.security.sanitize_variables(
            self.context.state.variables
        )
        package_copy.context.state.variables = sanitized_vars
        return package_copy
