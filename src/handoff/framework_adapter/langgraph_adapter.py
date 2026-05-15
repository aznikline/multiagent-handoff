"""LangGraph state adapter.

Converts between LangGraph checkpoint format and framework-agnostic
ContextPackage state.
"""

from __future__ import annotations

from typing import Any

from handoff.models.context import AgentState


class LangGraphAdapter:
    """Adapter for LangGraph framework state.

    LangGraph stores state as a flat dictionary per thread (checkpoint).
    Keys like ``messages``, ``__last_node__``, and user-defined variables
    are all mixed together.
    """

    # Well-known LangGraph internal keys that should not be treated as user state
    INTERNAL_KEYS: set[str] = {
        "__last_node__",
        "__run_id__",
        "__thread_id__",
        "__metadata__",
        "__parent_checkpoint__",
    }

    @classmethod
    def from_langgraph_state(cls, state: dict[str, Any]) -> AgentState:
        """Convert LangGraph state dict to framework-agnostic AgentState.

        Args:
            state: Raw LangGraph checkpoint state.

        Returns:
            Normalized AgentState with LangGraph-specific schema marker.
        """
        variables: dict[str, Any] = {}
        tool_history: list[dict[str, Any]] = []

        for key, value in state.items():
            if key in cls.INTERNAL_KEYS:
                continue
            if key == "messages":
                # LangGraph messages are typically BaseMessage objects
                # Convert to plain dicts for serialization
                variables[key] = cls._serialize_messages(value)
            elif key.startswith("__") and key.endswith("__"):
                # Skip other internal dunder keys
                continue
            else:
                variables[key] = value

        return AgentState(
            variables=variables,
            state_schema="langgraph",
            tool_call_history=tool_history,
        )

    @classmethod
    def to_langgraph_state(cls, agent_state: AgentState) -> dict[str, Any]:
        """Convert AgentState back to LangGraph-compatible state dict.

        Args:
            agent_state: Framework-agnostic state.

        Returns:
            LangGraph checkpoint-compatible dictionary.
        """
        result: dict[str, Any] = dict(agent_state.variables)

        # Restore messages if they were serialized
        if "messages" in result and isinstance(result["messages"], list):
            result["messages"] = cls._deserialize_messages(result["messages"])

        return result

    @staticmethod
    def _serialize_messages(messages: list[Any]) -> list[dict[str, Any]]:
        """Serialize LangGraph BaseMessage objects to plain dicts."""
        serialized: list[dict[str, Any]] = []
        for msg in messages:
            if hasattr(msg, "model_dump"):
                # Pydantic v2 BaseMessage
                serialized.append(msg.model_dump())
            elif hasattr(msg, "dict"):
                # Pydantic v1 BaseMessage
                serialized.append(msg.dict())
            elif isinstance(msg, dict):
                serialized.append(msg)
            else:
                serialized.append({"type": "unknown", "content": str(msg)})
        return serialized

    @staticmethod
    def _deserialize_messages(data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deserialize message dicts.

        Returns dicts rather than actual BaseMessage objects to avoid
        requiring langchain as a hard dependency.
        """
        return data
