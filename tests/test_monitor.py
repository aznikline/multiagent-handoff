"""Tests for HandoffMonitor automatic triggering."""

from __future__ import annotations

import pytest

from handoff.models.package import ContextPackage, PackageMeta, SourceInfo
from handoff.models.task import HandoffReason, TaskInfo
from handoff.monitor import HandoffMonitor, TokenThreshold
from handoff.orchestrator.orchestrator import HandoffOrchestrator
from handoff.orchestrator.selector import AgentDescriptor


class TestTokenThreshold:
    """Token threshold logic tests."""

    def test_default_thresholds(self) -> None:
        t = TokenThreshold(max_tokens=1000)
        assert t.should_warn(800) is True   # 80%
        assert t.should_warn(700) is False
        assert t.should_trigger(950) is True  # 95%
        assert t.should_trigger(940) is False

    def test_custom_thresholds(self) -> None:
        t = TokenThreshold(max_tokens=2000, warning_threshold=0.5, critical_threshold=0.9)
        assert t.should_warn(1000) is True
        assert t.should_warn(900) is False
        assert t.should_trigger(1800) is True
        assert t.should_trigger(1700) is False


class TestHandoffMonitor:
    """Monitor integration tests."""

    @pytest.fixture
    def orchestrator(self) -> HandoffOrchestrator:
        return HandoffOrchestrator()

    @pytest.fixture
    def monitor(self, orchestrator: HandoffOrchestrator) -> HandoffMonitor:
        return HandoffMonitor(
            orchestrator=orchestrator,
            token_threshold=TokenThreshold(max_tokens=100),
        )

    @pytest.fixture
    def package(self) -> ContextPackage:
        return ContextPackage(
            meta=PackageMeta(
                source=SourceInfo(agent_id="agent-a"),
                handoff_reason=HandoffReason.TOKEN_LIMIT,
            ),
            task=TaskInfo(original_task_id="task-1", description="Test"),
        )

    def test_record_and_get_usage(self, monitor: HandoffMonitor) -> None:
        monitor.record_token_usage("agent-1", 50)
        assert monitor.get_usage("agent-1") == 50
        assert monitor.get_usage("agent-2") == 0

    def test_reset_usage(self, monitor: HandoffMonitor) -> None:
        monitor.record_token_usage("agent-1", 50)
        monitor.reset("agent-1")
        assert monitor.get_usage("agent-1") == 0

    @pytest.mark.asyncio
    async def test_no_trigger_below_threshold(
        self, monitor: HandoffMonitor, package: ContextPackage
    ) -> None:
        monitor.record_token_usage("agent-a", 50)  # 50% of 100
        result = await monitor.check_and_trigger("agent-a", package)
        assert result is None

    @pytest.mark.asyncio
    async def test_trigger_at_critical_threshold(
        self, monitor: HandoffMonitor, package: ContextPackage
    ) -> None:
        monitor.record_token_usage("agent-a", 96)  # 96% of 100
        result = await monitor.check_and_trigger("agent-a", package)
        assert result is not None

    @pytest.mark.asyncio
    async def test_trigger_with_candidates(
        self, monitor: HandoffMonitor, package: ContextPackage
    ) -> None:
        monitor.record_token_usage("agent-a", 96)
        candidates = [
            AgentDescriptor(
                agent_id="agent-b",
                capabilities=frozenset({"research"}),
                current_load=0,
                max_concurrency=1,
            ),
        ]
        result = await monitor.check_and_trigger("agent-a", package, candidates=candidates)
        assert result is not None
        assert result.target_agent_id == "agent-b"

    @pytest.mark.asyncio
    async def test_trigger_callback_invoked(
        self, monitor: HandoffMonitor, package: ContextPackage
    ) -> None:
        calls = []

        def callback(agent_id: str, reason: HandoffReason, pkg: ContextPackage) -> None:
            calls.append((agent_id, reason, pkg.meta.package_id))

        monitor.on_trigger = callback
        monitor.record_token_usage("agent-a", 96)
        await monitor.check_and_trigger("agent-a", package)
        assert len(calls) == 1
        assert calls[0][0] == "agent-a"
        assert calls[0][1] == HandoffReason.TOKEN_LIMIT
