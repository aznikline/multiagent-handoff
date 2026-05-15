"""Context-related models: conversation, state, memory."""

from __future__ import annotations

from datetime import datetime

from handoff._utils import utc_now
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MessageRole(str, Enum):
    """Standard message roles in agent conversations."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ConversationMessage(BaseModel):
    """A single message in the conversation history."""

    role: MessageRole
    content: str
    timestamp: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TruncationStrategy(str, Enum):
    """Strategy used to truncate conversation history."""

    TAIL = "tail"
    SUMMARY = "summary"
    NONE = "none"


class ConversationState(BaseModel):
    """Conversation history with optional truncation metadata."""

    messages: list[ConversationMessage] = Field(default_factory=list)
    truncation: TruncationStrategy = Field(
        default=TruncationStrategy.NONE,
        description="Strategy used to fit messages into token budget.",
    )
    truncated_message_count: int = Field(
        default=0,
        ge=0,
        description="Number of messages removed or summarized.",
    )
    summary_prefix: str = Field(
        default="",
        description="Summary of conversation content before truncation point.",
    )


class AgentState(BaseModel):
    """Structured agent runtime state.

    The ``variables`` field uses a flat dictionary for framework-agnostic
    storage. Framework-specific adapters should document their key conventions.
    """

    variables: dict[str, Any] = Field(
        default_factory=dict,
        description="Framework-agnostic state variables.",
    )
    state_schema: str = Field(
        default="flat",
        description="Identifier for the state structure convention used.",
    )
    tool_call_history: list[dict[str, Any]] = Field(
        default_factory=list,
        description="History of tool invocations with arguments and results.",
    )
    pending_actions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Actions queued but not yet executed.",
    )


class MemorySnapshot(BaseModel):
    """Memory state at the time of handoff."""

    short_term: dict[str, Any] = Field(
        default_factory=dict,
        description="In-memory ephemeral context.",
    )
    long_term_keys: list[str] = Field(
        default_factory=list,
        description="References to external long-term memory entries.",
    )
