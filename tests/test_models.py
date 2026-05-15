"""Tests for handoff data models."""

from __future__ import annotations

from datetime import timedelta

import pytest

from handoff._utils import utc_now
from handoff.models.context import (
    AgentState,
    ConversationMessage,
    ConversationState,
    MemorySnapshot,
    MessageRole,
)
from handoff.models.package import (
    AgentFramework,
    CompatibilityMeta,
    ContextBody,
    ContextPackage,
    PackageMeta,
    SemanticVersion,
    SourceInfo,
    TruncationMeta,
)
from handoff.models.security import ClassificationLevel, SecurityMetadata
from handoff.models.task import (
    CheckpointRef,
    HandoffReason,
    Priority,
    ProgressSummary,
    TaskInfo,
    TaskStatus,
)


class TestSemanticVersion:
    """Semantic version parsing and comparison."""

    def test_parse_valid(self) -> None:
        v = SemanticVersion.parse("2.1.3")
        assert v.major == 2
        assert v.minor == 1
        assert v.patch == 3

    def test_parse_invalid(self) -> None:
        with pytest.raises(ValueError):
            SemanticVersion.parse("1.0")

    def test_str(self) -> None:
        assert str(SemanticVersion(major=1, minor=2, patch=3)) == "1.2.3"


class TestSecurityMetadata:
    """Security metadata and sanitization."""

    def test_default_whitelist_allows_all(self) -> None:
        sec = SecurityMetadata()
        assert sec.is_key_allowed("anything") is True

    def test_whitelist_blocks_unknown(self) -> None:
        sec = SecurityMetadata(allowed_variable_keys=["allowed"])
        assert sec.is_key_allowed("allowed") is True
        assert sec.is_key_allowed("secret") is False

    def test_sanitize_variables(self) -> None:
        sec = SecurityMetadata(allowed_variable_keys=["name", "count"])
        raw = {"name": "Alice", "secret": "password123", "count": 42}
        sanitized = sec.sanitize_variables(raw)
        assert "name" in sanitized
        assert "count" in sanitized
        assert "secret" not in sanitized
        assert "secret" in sec.redacted_keys
        assert sec.sanitized is True

    def test_scrub_pii_in_values(self) -> None:
        sec = SecurityMetadata(allowed_variable_keys=["email"])
        raw = {"email": "alice@example.com"}
        sanitized = sec.sanitize_variables(raw)
        assert "<EMAIL>" in sanitized["email"]

    def test_permission_ttl_bounds(self) -> None:
        with pytest.raises(ValueError):
            SecurityMetadata(permission_ttl_seconds=30)
        with pytest.raises(ValueError):
            SecurityMetadata(permission_ttl_seconds=90000)


class TestProgressSummary:
    """Progress summary generation and formatting."""

    def test_to_markdown(self) -> None:
        summary = ProgressSummary(
            completed_steps=["Step 1", "Step 2"],
            current_step="Step 3",
            key_intermediate_results="Found X",
            blockers="None",
            next_expected_action="Do Y",
        )
        md = summary.to_markdown()
        assert "已完成步骤" in md
        assert "Step 1" in md
        assert "Step 3" in md
        assert "Do Y" in md

    def test_empty_summary(self) -> None:
        summary = ProgressSummary()
        md = summary.to_markdown()
        assert "无" in md


class TestContextPackage:
    """Core context package operations."""

    @pytest.fixture
    def minimal_package(self) -> ContextPackage:
        return ContextPackage(
            meta=PackageMeta(
                source=SourceInfo(agent_id="agent-1"),
                handoff_reason=HandoffReason.TOKEN_LIMIT,
            ),
            task=TaskInfo(
                original_task_id="task-1",
                description="Test task",
            ),
        )

    def test_package_creation(self, minimal_package: ContextPackage) -> None:
        assert minimal_package.meta.spec_version == "1.0"
        assert minimal_package.meta.package_id
        assert minimal_package.meta.trace_id
        assert minimal_package.meta.source.agent_id == "agent-1"

    def test_is_expired_with_ttl(self, minimal_package: ContextPackage) -> None:
        minimal_package.meta.expires_at = utc_now() - timedelta(minutes=1)
        assert minimal_package.is_expired() is True

    def test_is_not_expired(self, minimal_package: ContextPackage) -> None:
        minimal_package.meta.expires_at = utc_now() + timedelta(minutes=10)
        assert minimal_package.is_expired() is False

    def test_is_expired_no_ttl(self, minimal_package: ContextPackage) -> None:
        minimal_package.meta.expires_at = None
        assert minimal_package.is_expired() is False

    def test_validate_security_no_whitelist_warning(self, minimal_package: ContextPackage) -> None:
        minimal_package.context.state.variables = {"key": "value"}
        errors = minimal_package.validate_security()
        assert any("warning" in e for e in errors)

    def test_validate_security_blocks_disallowed(self, minimal_package: ContextPackage) -> None:
        minimal_package.security.allowed_variable_keys = ["allowed"]
        minimal_package.context.state.variables = {"allowed": "ok", "secret": "bad"}
        errors = minimal_package.validate_security()
        assert any("secret" in e for e in errors)

    def test_sanitize_removes_disallowed(self, minimal_package: ContextPackage) -> None:
        minimal_package.security.allowed_variable_keys = ["name"]
        minimal_package.context.state.variables = {"name": "Alice", "secret": "x"}
        sanitized = minimal_package.sanitize()
        assert "name" in sanitized.context.state.variables
        assert "secret" not in sanitized.context.state.variables
        assert sanitized.security.sanitized is True

    def test_sanitize_is_immutable(self, minimal_package: ContextPackage) -> None:
        minimal_package.security.allowed_variable_keys = ["name"]
        minimal_package.context.state.variables = {"name": "Alice", "secret": "x"}
        original_vars = dict(minimal_package.context.state.variables)
        _ = minimal_package.sanitize()
        assert minimal_package.context.state.variables == original_vars

    def test_full_package_serialization(self) -> None:
        package = ContextPackage(
            meta=PackageMeta(
                source=SourceInfo(
                    agent_id="agent-a",
                    agent_role="researcher",
                    framework=AgentFramework.CREWAI,
                    version=SemanticVersion.parse("1.2.3"),
                ),
                handoff_reason=HandoffReason.TASK_DELEGATION,
                priority=Priority.HIGH,
                truncation=TruncationMeta(
                    applied=True,
                    strategy="tail",
                    truncated_message_count=5,
                    summary_prefix="Earlier: user asked for research",
                ),
            ),
            task=TaskInfo(
                original_task_id="task-42",
                description="Research quantum computing",
                expected_output_format='{"format": "markdown"}',
                status=TaskStatus.IN_PROGRESS,
                progress_summary=ProgressSummary(
                    completed_steps=["Search papers"],
                    current_step="Read paper #2",
                    next_expected_action="Extract results",
                ),
                checkpoint_ref=CheckpointRef(
                    native_ref="thread-123",
                    checkpoint_type="langgraph",
                ),
                required_capabilities=["research", "summarization"],
            ),
            context=ContextBody(
                conversation=ConversationState(
                    messages=[
                        ConversationMessage(role=MessageRole.USER, content="Hello"),
                        ConversationMessage(role=MessageRole.ASSISTANT, content="Hi there"),
                    ],
                ),
                state=AgentState(
                    variables={"topic": "quantum"},
                    state_schema="custom",
                    tool_call_history=[{"tool": "search", "result": "ok"}],
                ),
                memory=MemorySnapshot(
                    short_term={"context": "active"},
                    long_term_keys=["mem-1"],
                ),
            ),
            security=SecurityMetadata(
                classification=ClassificationLevel.CONFIDENTIAL,
                allowed_variable_keys=["topic"],
                encryption_at_rest=True,
            ),
            compatibility=CompatibilityMeta(
                source_schema_version="1.0",
                min_compatible_version="1.0",
            ),
        )

        # Verify all nested data is accessible
        assert package.meta.source.framework == AgentFramework.CREWAI
        assert package.task.checkpoint_ref.native_ref == "thread-123"
        assert len(package.context.conversation.messages) == 2
        assert package.context.memory.long_term_keys == ["mem-1"]
        assert package.security.classification == ClassificationLevel.CONFIDENTIAL
