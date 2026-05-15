"""Tests for context package serialization."""

from __future__ import annotations

import pytest

from handoff.models.package import ContextPackage, PackageMeta, SourceInfo
from handoff.models.task import HandoffReason, TaskInfo
from handoff.serialization.serializer import JsonSerializer, SerializationError


class TestJsonSerializer:
    """JSON serializer round-trip tests."""

    @pytest.fixture
    def serializer(self) -> JsonSerializer:
        return JsonSerializer()

    @pytest.fixture
    def sample_package(self) -> ContextPackage:
        return ContextPackage(
            meta=PackageMeta(
                source=SourceInfo(agent_id="agent-1"),
                handoff_reason=HandoffReason.USER_TRIGGERED,
            ),
            task=TaskInfo(
                original_task_id="task-1",
                description="Test task",
            ),
        )

    def test_round_trip(self, serializer: JsonSerializer, sample_package: ContextPackage) -> None:
        data = serializer.serialize(sample_package)
        assert isinstance(data, bytes)
        restored = serializer.deserialize(data)
        assert restored.meta.source.agent_id == "agent-1"
        assert restored.task.original_task_id == "task-1"
        assert restored.meta.package_id == sample_package.meta.package_id

    def test_serialized_is_valid_json(self, serializer: JsonSerializer, sample_package: ContextPackage) -> None:
        import json
        data = serializer.serialize(sample_package)
        parsed = json.loads(data)
        assert parsed["meta"]["spec_version"] == "1.0"
        assert parsed["task"]["original_task_id"] == "task-1"

    def test_deserialize_invalid_json(self, serializer: JsonSerializer) -> None:
        with pytest.raises(SerializationError, match="Invalid JSON"):
            serializer.deserialize(b"not json")

    def test_deserialize_empty_bytes(self, serializer: JsonSerializer) -> None:
        with pytest.raises(SerializationError):
            serializer.deserialize(b"")

    def test_forward_compatibility_unknown_fields(self, serializer: JsonSerializer) -> None:
        """Unknown fields in JSON should be ignored (forward compatibility)."""
        import json
        package = ContextPackage(
            meta=PackageMeta(
                source=SourceInfo(agent_id="agent-1"),
                handoff_reason=HandoffReason.USER_TRIGGERED,
            ),
            task=TaskInfo(
                original_task_id="task-1",
                description="Test",
            ),
        )
        data = serializer.serialize(package)
        parsed = json.loads(data)
        parsed["meta"]["future_field"] = "should_be_ignored"
        parsed["unknown_top_level"] = {"nested": "data"}
        modified = json.dumps(parsed).encode("utf-8")

        restored = serializer.deserialize(modified)
        assert restored.meta.source.agent_id == "agent-1"

    def test_complex_nested_round_trip(self, serializer: JsonSerializer) -> None:
        from handoff.models.context import (
            AgentState,
            ConversationMessage,
            ConversationState,
            MessageRole,
        )
        from handoff.models.security import SecurityMetadata

        package = ContextPackage(
            meta=PackageMeta(
                source=SourceInfo(agent_id="agent-2"),
                handoff_reason=HandoffReason.TOKEN_LIMIT,
            ),
            task=TaskInfo(
                original_task_id="task-2",
                description="Complex task",
            ),
            context=__import__("handoff.models.package", fromlist=["ContextBody"]).ContextBody(
                conversation=ConversationState(
                    messages=[
                        ConversationMessage(role=MessageRole.USER, content="Hello"),
                        ConversationMessage(
                            role=MessageRole.ASSISTANT,
                            content="Response",
                            metadata={"tool_calls": [{"id": "1"}]},
                        ),
                    ],
                ),
                state=AgentState(
                    variables={"counter": 42, "flag": True},
                ),
            ),
            security=SecurityMetadata(
                allowed_variable_keys=["counter", "flag"],
                sanitized=True,
            ),
        )

        data = serializer.serialize(package)
        restored = serializer.deserialize(data)
        assert len(restored.context.conversation.messages) == 2
        assert restored.context.state.variables["counter"] == 42
        assert restored.security.sanitized is True
