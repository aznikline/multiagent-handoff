"""Integration tests for handoff-relay service layer.

These tests exercise real round-trips through HandoffRelayService:
- create -> get -> list -> capture state
- Claude Code fake session flow
- Codex JSONL fixture parsing
- Local store expiry enforcement
- Error handling paths

All tests use tmp_path-based stores to avoid polluting ~/.handoff.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from handoff.models.task import HandoffReason
from handoff_relay.adapters.claude_code import ClaudeCodeAdapter
from handoff_relay.service import HandoffRelayService
from handoff_relay.storage.local_store import LocalHandoffStore


@pytest.fixture
def service(tmp_path: Path) -> HandoffRelayService:
    """Return a HandoffRelayService backed by a temp store."""
    store = LocalHandoffStore(base_dir=tmp_path / "store")
    return HandoffRelayService(store=store)


class TestServiceRoundTrip:
    """End-to-end package lifecycle via HandoffRelayService."""

    @pytest.mark.asyncio
    async def test_create_and_get_generic_package(self, service: HandoffRelayService) -> None:
        """Generic agent package: create -> get -> verify fields."""
        result = await service.create_package(
            source_agent="opencode",
            task_id="task-123",
            reason="token_limit",
            notes="Need to hand off",
        )
        assert "package_id" in result
        assert "summary" in result
        assert "file_path" in result

        package_id = result["package_id"]

        # Get in summary format
        got = await service.get_package(package_id)
        assert got["status"] == "stored"
        assert got["package_id"] == package_id

        # Get in full format
        full = await service.get_package(package_id, format="full")
        assert "package" in full
        assert full["package"]["task"]["original_task_id"] == "task-123"

    @pytest.mark.asyncio
    async def test_list_packages_with_filtering(self, service: HandoffRelayService) -> None:
        """Create multiple packages and filter by source agent."""
        await service.create_package("claude-code", "task-a", "user_triggered")
        await service.create_package("codex-cli", "task-b", "error_recovery")
        await service.create_package("opencode", "task-c", "token_limit")

        all_pkgs = await service.list_packages()
        assert all_pkgs["count"] == 3

        claude_only = await service.list_packages(source_agent="claude-code")
        assert claude_only["count"] == 1
        assert claude_only["packages"][0]["source_agent"] == "claude-code"

    @pytest.mark.asyncio
    async def test_capture_state_round_trip(self, service: HandoffRelayService) -> None:
        """Capture state -> save -> load -> verify context preserved."""
        result = await service.capture_state(
            agent_type="claude-code",
            messages=[
                {"role": "user", "content": "Fix the bug"},
                {"role": "assistant", "content": "Found it in line 42"},
            ],
            variables={"debug_mode": True, "file": "main.py"},
            current_step="Fixing bug in main.py",
            blockers=["Need review"],
        )
        assert result["status"] == "captured"
        capture_id = result["capture_id"]

        # Load back and verify context
        loaded = await service.get_package(capture_id, format="full")
        pkg = loaded["package"]
        assert pkg["task"]["description"] == "Fixing bug in main.py"
        assert len(pkg["context"]["conversation"]["messages"]) == 2
        assert pkg["context"]["state"]["variables"]["debug_mode"] is True
        assert pkg["context"]["state"]["variables"]["file"] == "main.py"

    @pytest.mark.asyncio
    async def test_get_injectable_context(self, service: HandoffRelayService) -> None:
        """Create package -> get injectable format."""
        result = await service.create_package(
            source_agent="claude-code",
            task_id="task-inject",
            reason="capability_mismatch",
            notes="Switching to specialist agent",
        )
        package_id = result["package_id"]

        injectable = await service.get_injectable_context(
            package_id=package_id,
            target_agent="claude-code",
        )
        assert "injectable_markdown" in injectable
        assert "system_prompt_addition" in injectable
        assert package_id in injectable["injectable_markdown"]

    @pytest.mark.asyncio
    async def test_get_missing_package(self, service: HandoffRelayService) -> None:
        """Requesting a non-existent package returns error dict."""
        result = await service.get_package("nonexistent-id")
        assert "error" in result
        assert "nonexistent-id" in result["error"]

    @pytest.mark.asyncio
    async def test_get_injectable_missing_package(self, service: HandoffRelayService) -> None:
        """Injectable context for missing package returns error dict."""
        result = await service.get_injectable_context(
            package_id="missing-id",
            target_agent="claude-code",
        )
        assert "error" in result


class TestClaudeCodeFakeSessionFlow:
    """Full Claude Code flow with fake session data."""

    @pytest.mark.asyncio
    async def test_fake_session_create_inject_cleanup(
        self,
        service: HandoffRelayService,
        tmp_path: Path,
    ) -> None:
        """Write fake session JSON -> create package -> inject -> cleanup."""
        from handoff_relay.adapters.session_parser import ClaudeCodeSessionParser

        # 1. Create fake Claude Code session
        session_dir = tmp_path / "sessions"
        session_dir.mkdir()
        session_file = session_dir / "session-001.json"
        session_data = [
            {"role": "user", "content": "Implement auth"},
            {"role": "assistant", "content": "Added login endpoint"},
            {"role": "user", "content": "Add tests too"},
            {"role": "assistant", "content": "Added 5 test cases"},
        ]
        session_file.write_text(json.dumps(session_data), encoding="utf-8")

        # 2. Parse and create package
        parser = ClaudeCodeSessionParser(session_dir=session_dir)
        snapshot = parser.parse(session_file)
        assert len(snapshot.messages) == 4
        assert snapshot.current_task == "Implement auth"
        assert "Added 5 test cases" in snapshot.last_assistant_message

        # 3. Create package via service (generic path since source != claude-code adapter)
        # Actually, use the adapter directly for the real claude path
        adapter = ClaudeCodeAdapter(store=service._store)
        result = await adapter.create_package(
            task_id="auth-feature",
            reason=HandoffReason.TOKEN_LIMIT,
            notes="Session has 4 messages",
        )
        package_id = result["package_id"]

        # 4. Inject into project CLAUDE.md
        project = tmp_path / "project"
        project.mkdir()
        claude_md = project / "CLAUDE.md"
        claude_md.write_text("# Project\n\n", encoding="utf-8")

        injected_path = adapter.inject_into_claude_md(package_id, project)
        assert injected_path == claude_md
        text = claude_md.read_text(encoding="utf-8")
        assert package_id in text

        # 5. Cleanup removes the block
        cleaned = adapter.cleanup_claude_md(project)
        assert cleaned is True
        assert package_id not in claude_md.read_text(encoding="utf-8")


class TestCodexJSONLFixture:
    """Codex JSONL parsing with fixture data."""

    def test_parse_codex_jsonl_with_nested_messages(self, tmp_path: Path) -> None:
        """Codex JSONL with nested message structures is parsed correctly."""
        from handoff_relay.adapters.session_parser import CodexSessionParser

        session_dir = tmp_path / "codex_sessions"
        session_dir.mkdir(parents=True)
        session_file = session_dir / "2024" / "01" / "15" / "rollout.jsonl"
        session_file.parent.mkdir(parents=True)

        lines = [
            json.dumps({
                "type": "rollout",
                "messages": [
                    {"role": "user", "content": "Refactor utils"},
                    {"role": "assistant", "content": "Done"},
                ],
            }),
            json.dumps({
                "state": {"cwd": "/home/user/project"},
                "output": {
                    "role": "output",
                    "content": "Final result: 42",
                },
            }),
        ]
        session_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        parser = CodexSessionParser(session_dir=session_dir)
        snapshot = parser.parse(session_file)

        # Should extract messages from both top-level and nested structures
        assert len(snapshot.messages) >= 2
        roles = [m["role"] for m in snapshot.messages]
        assert "user" in roles
        assert "assistant" in roles

        # state variables should be captured
        assert snapshot.state_variables.get("cwd") == "/home/user/project"


class TestLocalStoreExpiry:
    """Expiry enforcement on save, load, and cleanup."""

    @pytest.mark.asyncio
    async def test_expired_package_not_loadable(self, tmp_path: Path) -> None:
        """Package with past TTL is rejected on load and deleted."""
        from datetime import timedelta
        from handoff._utils import utc_now

        store = LocalHandoffStore(base_dir=tmp_path / "store")
        service = HandoffRelayService(store=store)

        # Create package with very short TTL by setting expires_at directly
        result = await service.capture_state(
            agent_type="claude-code",
            messages=[{"role": "user", "content": "Hello"}],
            variables={},
            current_step="Test",
            blockers=[],
        )
        capture_id = result["capture_id"]

        # Manually expire it by patching the DB row
        past = (utc_now() - timedelta(seconds=1)).isoformat()
        import sqlite3
        conn = sqlite3.connect(store._db_path)
        conn.execute(
            "UPDATE packages SET expires_at = ? WHERE package_id = ?",
            (past, capture_id),
        )
        conn.commit()
        conn.close()

        # Load should return None and clean up
        loaded = await store.load(capture_id)
        assert loaded is None

        # File should be gone
        assert not store._package_path(capture_id).exists()

    @pytest.mark.asyncio
    async def test_cleanup_expired_removes_packages(self, tmp_path: Path) -> None:
        """cleanup_expired removes only expired packages."""
        from datetime import timedelta
        from handoff._utils import utc_now

        store = LocalHandoffStore(base_dir=tmp_path / "store")
        service = HandoffRelayService(store=store)

        # Create two packages
        r1 = await service.capture_state("a", [], {}, "fresh", [])
        r2 = await service.capture_state("a", [], {}, "expired", [])

        # Expire the second one
        past = (utc_now() - timedelta(seconds=1)).isoformat()
        import sqlite3
        conn = sqlite3.connect(store._db_path)
        conn.execute(
            "UPDATE packages SET expires_at = ? WHERE package_id = ?",
            (past, r2["capture_id"]),
        )
        conn.commit()
        conn.close()

        count = await service.cleanup_expired()
        assert count == 1

        # Fresh package still loadable
        assert await store.load(r1["capture_id"]) is not None
        # Expired package gone
        assert await store.load(r2["capture_id"]) is None


class TestReasonNormalization:
    """Legacy reason aliases are normalized through the service layer."""

    @pytest.mark.asyncio
    async def test_manual_reason_normalized(self, service: HandoffRelayService) -> None:
        """'manual' is accepted and maps to user_triggered."""
        result = await service.create_package(
            source_agent="opencode",
            task_id="task-manual",
            reason="manual",
        )
        pkg = await service.get_package(result["package_id"], format="full")
        assert pkg["package"]["meta"]["handoff_reason"] == "user_triggered"

    @pytest.mark.asyncio
    async def test_error_reason_normalized(self, service: HandoffRelayService) -> None:
        """'error' is accepted and maps to error_recovery."""
        result = await service.create_package(
            source_agent="opencode",
            task_id="task-error",
            reason="error",
        )
        pkg = await service.get_package(result["package_id"], format="full")
        assert pkg["package"]["meta"]["handoff_reason"] == "error_recovery"

    @pytest.mark.asyncio
    async def test_invalid_reason_raises(self, service: HandoffRelayService) -> None:
        """Truly invalid reasons fail fast."""
        with pytest.raises(ValueError):
            await service.create_package(
                source_agent="opencode",
                task_id="task-bad",
                reason="totally_invalid",
            )
