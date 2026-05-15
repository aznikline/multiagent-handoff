"""Tests for A2A Protocol adapter."""

from __future__ import annotations

import pytest

from handoff.a2a_adapter.mapper import (
    A2AHandoffClient,
    build_agent_card,
    from_a2a_task,
    to_a2a_task,
)
from handoff.models.context import ConversationMessage, ConversationState, MessageRole
from handoff.models.package import ContextBody, ContextPackage, PackageMeta, SourceInfo
from handoff.models.task import HandoffReason, ProgressSummary, TaskInfo


class TestA2AAdapter:
    """A2A bidirectional mapping tests."""

    @pytest.fixture
    def sample_package(self) -> ContextPackage:
        return ContextPackage(
            meta=PackageMeta(
                source=SourceInfo(agent_id="source-agent", agent_role="researcher"),
                handoff_reason=HandoffReason.TOKEN_LIMIT,
            ),
            task=TaskInfo(
                original_task_id="task-1",
                description="Research quantum",
                progress_summary=ProgressSummary(
                    completed_steps=["Step 1"],
                    current_step="Step 2",
                    next_expected_action="Step 3",
                ),
                required_capabilities=["research"],
            ),
            context=ContextBody(
                conversation=ConversationState(
                    messages=[
                        ConversationMessage(role=MessageRole.USER, content="Hello"),
                    ],
                ),
            ),
        )

    def test_to_a2a_task_structure(self, sample_package: ContextPackage) -> None:
        task = to_a2a_task(sample_package)
        assert task["id"] == sample_package.meta.package_id
        assert task["sessionId"] == sample_package.meta.trace_id
        assert task["metadata"]["operation"] == "handoff_resume"
        assert len(task["artifacts"]) == 1
        assert task["artifacts"][0]["name"] == "context-package"

    def test_to_a2a_task_artifact_contains_payload(self, sample_package: ContextPackage) -> None:
        task = to_a2a_task(sample_package)
        artifact = task["artifacts"][0]
        parts = artifact["parts"]
        assert len(parts) == 1
        data = parts[0]["data"]
        assert data["mimeType"] == "application/json"
        assert "content" in data
        assert len(data["content"]) > 0

    def test_round_trip(self, sample_package: ContextPackage) -> None:
        task = to_a2a_task(sample_package)
        restored = from_a2a_task(task)
        assert restored.meta.source.agent_id == "source-agent"
        assert restored.task.original_task_id == "task-1"
        assert restored.task.required_capabilities == ["research"]

    def test_from_a2a_task_missing_artifact_raises(self) -> None:
        with pytest.raises(ValueError, match="context-package"):
            from_a2a_task({"id": "x", "artifacts": []})

    def test_build_agent_card(self) -> None:
        card = build_agent_card(
            name="HandoffAgent",
            url="https://example.com/agent",
            capabilities=["research", "code"],
        )
        assert card["name"] == "HandoffAgent"
        assert card["url"] == "https://example.com/agent"
        assert any(s["id"] == "context-handoff" for s in card["skills"])


class TestA2AHandoffClient:
    """A2A client wrapper tests."""

    def test_init_requires_client(self) -> None:
        mock_client = type("Mock", (), {})()
        client = A2AHandoffClient(mock_client)
        assert client._client is mock_client
