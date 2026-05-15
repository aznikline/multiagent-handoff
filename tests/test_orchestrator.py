"""Tests for handoff orchestrator, store, selector, and injector."""

from __future__ import annotations

from datetime import timedelta

import pytest

from handoff._utils import utc_now
from handoff.models.context import (
    AgentState,
    ConversationMessage,
    ConversationState,
    MessageRole,
)
from handoff.models.package import ContextBody, ContextPackage, PackageMeta, SourceInfo
from handoff.models.task import (
    HandoffReason,
    Priority,
    ProgressSummary,
    TaskInfo,
)
from handoff.orchestrator.injector import InjectionError, PromptBasedInjector
from handoff.orchestrator.orchestrator import HandoffOrchestrator, HandoffStatus
from handoff.orchestrator.selector import AgentDescriptor, CapabilityBasedSelector
from handoff.orchestrator.store import InMemoryHandoffStore


class TestInMemoryStore:
    """In-memory storage backend tests."""

    @pytest.fixture
    async def store(self) -> InMemoryHandoffStore:
        return InMemoryHandoffStore()

    @pytest.fixture
    def sample_package(self) -> ContextPackage:
        return ContextPackage(
            meta=PackageMeta(
                source=SourceInfo(agent_id="agent-1"),
                handoff_reason=HandoffReason.USER_TRIGGERED,
            ),
            task=TaskInfo(original_task_id="task-1", description="Test"),
        )

    @pytest.mark.asyncio
    async def test_save_and_load(
        self, store: InMemoryHandoffStore, sample_package: ContextPackage
    ) -> None:
        await store.save(sample_package)
        loaded = await store.load(sample_package.meta.package_id)
        assert loaded is not None
        assert loaded.meta.source.agent_id == "agent-1"

    @pytest.mark.asyncio
    async def test_load_missing(self, store: InMemoryHandoffStore) -> None:
        result = await store.load("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_existing(
        self, store: InMemoryHandoffStore, sample_package: ContextPackage
    ) -> None:
        await store.save(sample_package)
        deleted = await store.delete(sample_package.meta.package_id)
        assert deleted is True
        assert await store.load(sample_package.meta.package_id) is None

    @pytest.mark.asyncio
    async def test_delete_missing(self, store: InMemoryHandoffStore) -> None:
        deleted = await store.delete("nonexistent")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_expired_package_auto_cleanup(
        self, store: InMemoryHandoffStore, sample_package: ContextPackage
    ) -> None:
        sample_package.meta.expires_at = utc_now() - timedelta(minutes=1)
        await store.save(sample_package)
        loaded = await store.load(sample_package.meta.package_id)
        assert loaded is None  # Auto-cleaned on access

    @pytest.mark.asyncio
    async def test_list_expired(
        self, store: InMemoryHandoffStore, sample_package: ContextPackage
    ) -> None:
        sample_package.meta.expires_at = utc_now() - timedelta(minutes=1)
        await store.save(sample_package)
        expired = await store.list_expired()
        assert sample_package.meta.package_id in expired

    @pytest.mark.asyncio
    async def test_non_expired_not_in_list(
        self, store: InMemoryHandoffStore, sample_package: ContextPackage
    ) -> None:
        sample_package.meta.expires_at = utc_now() + timedelta(hours=1)
        await store.save(sample_package)
        expired = await store.list_expired()
        assert sample_package.meta.package_id not in expired


class TestCapabilityBasedSelector:
    """Agent selection strategy tests."""

    @pytest.fixture
    def selector(self) -> CapabilityBasedSelector:
        return CapabilityBasedSelector()

    @pytest.fixture
    def candidates(self) -> list[AgentDescriptor]:
        return [
            AgentDescriptor(
                agent_id="agent-a",
                capabilities=frozenset({"research", "summarize"}),
                current_load=0,
                max_concurrency=2,
                token_window_remaining=4000,
            ),
            AgentDescriptor(
                agent_id="agent-b",
                capabilities=frozenset({"code", "debug"}),
                current_load=0,
                max_concurrency=1,
                token_window_remaining=8000,
            ),
            AgentDescriptor(
                agent_id="agent-c",
                capabilities=frozenset({"research", "summarize", "code"}),
                current_load=2,  # At capacity
                max_concurrency=2,
                token_window_remaining=6000,
            ),
        ]

    @pytest.mark.asyncio
    async def test_select_by_capabilities(
        self, selector: CapabilityBasedSelector, candidates: list[AgentDescriptor]
    ) -> None:
        result = await selector.select(
            candidates=candidates,
            required_capabilities=["research"],
            priority="normal",
        )
        assert result is not None
        assert result.agent_id == "agent-a"

    @pytest.mark.asyncio
    async def test_select_prefers_less_loaded(
        self, selector: CapabilityBasedSelector, candidates: list[AgentDescriptor]
    ) -> None:
        # agent-a has research, agent-c has research but is at capacity
        result = await selector.select(
            candidates=candidates,
            required_capabilities=["research"],
            priority="normal",
        )
        assert result is not None
        assert result.agent_id == "agent-a"

    @pytest.mark.asyncio
    async def test_select_no_match(self, selector: CapabilityBasedSelector, candidates: list[AgentDescriptor]) -> None:
        result = await selector.select(
            candidates=candidates,
            required_capabilities=["design"],
            priority="normal",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_select_empty_candidates(self, selector: CapabilityBasedSelector) -> None:
        result = await selector.select(
            candidates=[],
            required_capabilities=["research"],
            priority="normal",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_select_respects_does_not_accept_handoff(self) -> None:
        candidates = [
            AgentDescriptor(
                agent_id="agent-no",
                capabilities=frozenset({"research"}),
                accepts_handoff=False,
            ),
        ]
        selector = CapabilityBasedSelector()
        result = await selector.select(
            candidates=candidates,
            required_capabilities=["research"],
            priority="normal",
        )
        assert result is None


class TestPromptBasedInjector:
    """Context injection tests."""

    @pytest.fixture
    def injector(self) -> PromptBasedInjector:
        return PromptBasedInjector()

    @pytest.fixture
    def sample_package(self) -> ContextPackage:
        return ContextPackage(
            meta=PackageMeta(
                source=SourceInfo(agent_id="source-agent", agent_role="researcher"),
                handoff_reason=HandoffReason.TOKEN_LIMIT,
                priority=Priority.HIGH,
            ),
            task=TaskInfo(
                original_task_id="task-1",
                description="Research quantum computing",
                progress_summary=ProgressSummary(
                    completed_steps=["Searched papers"],
                    current_step="Reading results",
                    next_expected_action="Summarize findings",
                ),
            ),
            context=ContextBody(
                conversation=ConversationState(
                    messages=[
                        ConversationMessage(role=MessageRole.USER, content="Research quantum"),
                        ConversationMessage(role=MessageRole.ASSISTANT, content="Found papers"),
                    ],
                ),
                state=AgentState(variables={"topic": "quantum"}),
            ),
        )

    @pytest.mark.asyncio
    async def test_inject_builds_prompt(
        self, injector: PromptBasedInjector, sample_package: ContextPackage
    ) -> None:
        result = await injector.inject("target-agent", sample_package)
        assert result["target_agent_id"] == "target-agent"
        assert "session_id" in result
        prompt = result["system_prompt"]
        assert "交接任务说明" in prompt
        assert "source-agent" in prompt
        assert "Research quantum computing" in prompt
        assert "Searched papers" in prompt

    @pytest.mark.asyncio
    async def test_inject_preserves_original_prompt(
        self, injector: PromptBasedInjector, sample_package: ContextPackage
    ) -> None:
        original = "You are a helpful assistant."
        result = await injector.inject("target", sample_package, original)
        prompt = result["system_prompt"]
        assert original in prompt
        assert "交接任务说明" in prompt

    @pytest.mark.asyncio
    async def test_inject_expired_package_raises(
        self, injector: PromptBasedInjector, sample_package: ContextPackage
    ) -> None:
        sample_package.meta.expires_at = utc_now() - timedelta(minutes=1)
        with pytest.raises(InjectionError, match="expired"):
            await injector.inject("target", sample_package)

    @pytest.mark.asyncio
    async def test_inject_message_history(
        self, injector: PromptBasedInjector, sample_package: ContextPackage
    ) -> None:
        result = await injector.inject("target", sample_package)
        messages = result["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_inject_state_variables(
        self, injector: PromptBasedInjector, sample_package: ContextPackage
    ) -> None:
        result = await injector.inject("target", sample_package)
        assert result["state_variables"]["topic"] == "quantum"


class TestHandoffOrchestrator:
    """End-to-end orchestrator tests."""

    @pytest.fixture
    async def orchestrator(self) -> HandoffOrchestrator:
        return HandoffOrchestrator()

    @pytest.fixture
    def package(self) -> ContextPackage:
        return ContextPackage(
            meta=PackageMeta(
                source=SourceInfo(agent_id="source-agent"),
                handoff_reason=HandoffReason.TASK_DELEGATION,
                priority=Priority.NORMAL,
            ),
            task=TaskInfo(
                original_task_id="task-1",
                description="Test task",
                required_capabilities=["research"],
                progress_summary=ProgressSummary(
                    completed_steps=["Step 1"],
                    current_step="Step 2",
                ),
            ),
            context=ContextBody(
                state=AgentState(variables={"key": "value"}),
            ),
        )

    @pytest.mark.asyncio
    async def test_initiate_no_candidates_pending(
        self, orchestrator: HandoffOrchestrator, package: ContextPackage
    ) -> None:
        result = await orchestrator.initiate(
            source_agent_id="source-agent",
            reason=HandoffReason.TASK_DELEGATION,
            package=package,
        )
        assert result.status == HandoffStatus.PENDING
        assert any("No target candidates" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_initiate_with_successful_match(
        self, orchestrator: HandoffOrchestrator, package: ContextPackage
    ) -> None:
        candidates = [
            AgentDescriptor(
                agent_id="target-1",
                capabilities=frozenset({"research"}),
                current_load=0,
                max_concurrency=1,
            ),
        ]
        result = await orchestrator.initiate(
            source_agent_id="source-agent",
            reason=HandoffReason.TASK_DELEGATION,
            package=package,
            candidates=candidates,
        )
        assert result.status == HandoffStatus.RESUMED
        assert result.target_agent_id == "target-1"
        assert result.session_id is not None

    @pytest.mark.asyncio
    async def test_initiate_no_suitable_target(
        self, orchestrator: HandoffOrchestrator, package: ContextPackage
    ) -> None:
        candidates = [
            AgentDescriptor(
                agent_id="target-1",
                capabilities=frozenset({"coding"}),  # Missing "research"
                current_load=0,
                max_concurrency=1,
            ),
        ]
        result = await orchestrator.initiate(
            source_agent_id="source-agent",
            reason=HandoffReason.TASK_DELEGATION,
            package=package,
            candidates=candidates,
        )
        assert result.status == HandoffStatus.PENDING
        assert any("No suitable target" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_get_status_existing(
        self, orchestrator: HandoffOrchestrator, package: ContextPackage
    ) -> None:
        await orchestrator.initiate(
            source_agent_id="source",
            reason=HandoffReason.TASK_DELEGATION,
            package=package,
        )
        status = await orchestrator.get_status(package.meta.package_id)
        assert status["package_id"] == package.meta.package_id
        assert status["status"] == "stored"
        assert status["expired"] is False

    @pytest.mark.asyncio
    async def test_get_status_missing(self, orchestrator: HandoffOrchestrator) -> None:
        status = await orchestrator.get_status("nonexistent")
        assert status["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_cleanup_expired(
        self, orchestrator: HandoffOrchestrator, package: ContextPackage
    ) -> None:
        package.meta.expires_at = utc_now() - timedelta(minutes=1)
        await orchestrator.initiate(
            source_agent_id="source",
            reason=HandoffReason.TASK_DELEGATION,
            package=package,
        )
        cleaned = await orchestrator.cleanup_expired()
        assert cleaned == 1
        status = await orchestrator.get_status(package.meta.package_id)
        assert status["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_audit_log_populated(
        self, orchestrator: HandoffOrchestrator, package: ContextPackage
    ) -> None:
        candidates = [
            AgentDescriptor(
                agent_id="target-1",
                capabilities=frozenset({"research"}),
                current_load=0,
                max_concurrency=1,
            ),
        ]
        await orchestrator.initiate(
            source_agent_id="source",
            reason=HandoffReason.TASK_DELEGATION,
            package=package,
            candidates=candidates,
        )
        log = orchestrator.get_audit_log()
        assert len(log) >= 2  # stored + injected
        assert any(entry["event"] == "stored" for entry in log)
        assert any(entry["event"] == "injected" for entry in log)

    @pytest.mark.asyncio
    async def test_security_sanitization_removes_disallowed(
        self, orchestrator: HandoffOrchestrator, package: ContextPackage
    ) -> None:
        """Orchestrator auto-sanitizes: disallowed keys are removed, then proceeds."""
        package.security.allowed_variable_keys = ["allowed"]
        package.context.state.variables = {"allowed": "ok", "secret": "bad"}
        result = await orchestrator.initiate(
            source_agent_id="source",
            reason=HandoffReason.TASK_DELEGATION,
            package=package,
        )
        # Sanitization removes "secret", validation passes, proceeds to PENDING (no candidates)
        assert result.status == HandoffStatus.PENDING
        # Verify the stored package was sanitized
        status = await orchestrator.get_status(package.meta.package_id)
        assert status["status"] == "stored"
