"""Regression tests for handoff-relay components."""

from __future__ import annotations

from pathlib import Path

import pytest

from handoff.models.context import MessageRole
from handoff.models.task import HandoffReason
from handoff_relay._utils import normalize_reason
from handoff_relay.adapters.claude_code import ClaudeCodeAdapter, HANDOFF_BLOCK_START
from handoff_relay.storage.local_store import LocalHandoffStore


class TestNormalizeReason:
    """Regression tests for reason normalization."""

    def test_manual_alias(self) -> None:
        assert normalize_reason("manual") == "user_triggered"

    def test_rate_limit_alias(self) -> None:
        assert normalize_reason("rate_limit") == "user_triggered"

    def test_error_alias(self) -> None:
        assert normalize_reason("error") == "error_recovery"

    def test_valid_reason_passthrough(self) -> None:
        assert normalize_reason("token_limit") == "token_limit"
        assert normalize_reason("user_triggered") == "user_triggered"
        assert normalize_reason("error_recovery") == "error_recovery"
        assert normalize_reason("capability_mismatch") == "capability_mismatch"
        assert normalize_reason("scheduled") == "scheduled"

    def test_unknown_reason_passthrough(self) -> None:
        assert normalize_reason("unknown") == "unknown"


class TestClaudeCodeAdapterSymlinkCleanup:
    """Regression tests for symlink-safe CLAUDE.md cleanup."""

    @pytest.fixture
    def adapter(self, tmp_path: Path) -> ClaudeCodeAdapter:
        store = LocalHandoffStore(base_dir=tmp_path / "store")
        return ClaudeCodeAdapter(store=store)

    def test_cleanup_removes_from_claude_md_subdirectory(
        self, tmp_path: Path, adapter: ClaudeCodeAdapter
    ) -> None:
        """When CLAUDE.md is a symlink to AGENTS.md, cleanup must target .claude/CLAUDE.md."""
        # Setup: AGENTS.md exists, CLAUDE.md is symlink to it
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text("# Agents\n\nShared instructions.\n", encoding="utf-8")

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.symlink_to("AGENTS.md")

        # Inject handoff block (should go to .claude/CLAUDE.md)
        injected_path = adapter.inject_into_claude_md("pkg-123", tmp_path)

        # Verify injection went to .claude/CLAUDE.md, not the symlink
        assert injected_path == tmp_path / ".claude" / "CLAUDE.md"
        assert HANDOFF_BLOCK_START in injected_path.read_text(encoding="utf-8")
        # Symlink target must NOT be polluted
        assert HANDOFF_BLOCK_START not in agents_md.read_text(encoding="utf-8")

        # Cleanup must remove block from .claude/CLAUDE.md
        cleaned = adapter.cleanup_claude_md(tmp_path)
        assert cleaned is True
        assert HANDOFF_BLOCK_START not in injected_path.read_text(encoding="utf-8")

    def test_cleanup_falls_back_to_regular_claude_md(
        self, tmp_path: Path, adapter: ClaudeCodeAdapter
    ) -> None:
        """When CLAUDE.md is a regular file, cleanup targets it directly."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Project\n\nInstructions.\n", encoding="utf-8")

        adapter.inject_into_claude_md("pkg-456", tmp_path)

        assert HANDOFF_BLOCK_START in claude_md.read_text(encoding="utf-8")

        cleaned = adapter.cleanup_claude_md(tmp_path)
        assert cleaned is True
        assert HANDOFF_BLOCK_START not in claude_md.read_text(encoding="utf-8")

    def test_cleanup_returns_false_when_no_block(
        self, tmp_path: Path, adapter: ClaudeCodeAdapter
    ) -> None:
        """Cleanup returns False when no handoff block exists."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Project\n\nNo handoff block here.\n", encoding="utf-8")

        cleaned = adapter.cleanup_claude_md(tmp_path)
        assert cleaned is False


class TestHandoffCaptureState:
    """Regression tests for handoff_capture_state context persistence."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> LocalHandoffStore:
        return LocalHandoffStore(base_dir=tmp_path)

    @pytest.mark.asyncio
    async def test_capture_state_persists_messages_and_variables(self, store: LocalHandoffStore) -> None:
        """Captured messages and variables must be retrievable via handoff_get_package."""
        import uuid
        from handoff_relay._builders import build_capture_package

        capture_id = f"capture-{uuid.uuid4().hex[:8]}"
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
            {"role": "model", "content": "Model msg"},
            {"role": "unknown", "content": "Fallback"},
        ]
        variables = {"counter": 42, "flag": True, "name": "test"}
        current_step = "Implementing feature X"
        blockers = ["API rate limited"]

        package = build_capture_package(
            capture_id=capture_id,
            agent_type="claude-code",
            messages=messages,
            variables=variables,
            current_step=current_step,
            blockers=blockers,
        )

        await store.save(package)

        # Reload and verify
        loaded = await store.load(capture_id)
        assert loaded is not None

        # Messages must be preserved
        assert len(loaded.context.conversation.messages) == 4
        assert loaded.context.conversation.messages[0].role == MessageRole.USER
        assert loaded.context.conversation.messages[0].content == "Hello"
        assert loaded.context.conversation.messages[1].role == MessageRole.ASSISTANT
        assert loaded.context.conversation.messages[1].content == "Hi there"
        assert loaded.context.conversation.messages[2].role == MessageRole.ASSISTANT  # model maps to assistant
        assert loaded.context.conversation.messages[2].content == "Model msg"
        assert loaded.context.conversation.messages[3].role == MessageRole.USER  # unknown maps to user
        assert loaded.context.conversation.messages[3].content == "Fallback"

        # Variables must be preserved as structured data
        assert loaded.context.state.variables == variables
        assert loaded.context.state.variables["counter"] == 42
        assert loaded.context.state.variables["flag"] is True
        assert loaded.context.state.variables["name"] == "test"

        # key_intermediate_results should NOT be a dumped JSON string
        assert "json" not in loaded.task.progress_summary.key_intermediate_results.lower()

    @pytest.mark.asyncio
    async def test_capture_state_empty_messages_and_variables(self, store: LocalHandoffStore) -> None:
        """Capture with empty messages/variables should still produce valid package."""
        import uuid
        from handoff_relay._builders import build_capture_package

        capture_id = f"capture-{uuid.uuid4().hex[:8]}"

        package = build_capture_package(
            capture_id=capture_id,
            agent_type="opencode",
            messages=[],
            variables={},
            current_step="",
            blockers=[],
        )

        await store.save(package)
        loaded = await store.load(capture_id)
        assert loaded is not None
        assert loaded.context.conversation.messages == []
        assert loaded.context.state.variables == {}


class TestMCPReasonNormalization:
    """Regression tests ensuring MCP tools normalize legacy reasons."""

    def test_normalize_reason_used_in_mcp_create_path(self) -> None:
        """Legacy reason strings must map to valid enum values."""
        # Verify the normalization function handles all legacy aliases
        for raw, expected in [
            ("manual", "user_triggered"),
            ("rate_limit", "user_triggered"),
            ("error", "error_recovery"),
        ]:
            normalized = normalize_reason(raw)
            # Should not raise when constructing HandoffReason
            HandoffReason(normalized)

    def test_invalid_reason_still_raises(self) -> None:
        """Truly invalid reasons should still fail fast after normalization."""
        with pytest.raises(ValueError):
            HandoffReason(normalize_reason("totally_invalid_reason"))
