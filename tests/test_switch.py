"""Tests for agent switching feature — detect, switch, inject, launch."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from handoff.models.task import HandoffReason
from handoff_relay._utils import detect_current_agent
from handoff_relay.adapters.codex import CodexAdapter
from handoff_relay.service import HandoffRelayService
from handoff_relay.storage.local_store import LocalHandoffStore


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def service(tmp_path: Path) -> HandoffRelayService:
    """Return a HandoffRelayService backed by a temp store."""
    store = LocalHandoffStore(base_dir=tmp_path / "store")
    return HandoffRelayService(store=store)


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """A fake project directory with CLAUDE.md and AGENTS.md."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text("# Project\n\n", encoding="utf-8")
    (project / "AGENTS.md").write_text("# Agents\n\n", encoding="utf-8")
    return project


# ── Auto-Detection ────────────────────────────────────────────────────


class TestDetectCurrentAgent:
    """Auto-detect current agent from session directory timestamps."""

    def test_detects_claude_code(self, tmp_path: Path) -> None:
        """Most recent session in ~/.claude/sessions wins."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        claude_sessions = fake_home / ".claude" / "sessions"
        claude_sessions.mkdir(parents=True)
        (claude_sessions / "session.json").write_text("{}", encoding="utf-8")

        codex_sessions = fake_home / ".codex" / "sessions"
        codex_sessions.mkdir(parents=True)
        (codex_sessions / "old.jsonl").write_text("{}", encoding="utf-8")

        # Make codex file older
        old_time = 1000.0
        import os
        os.utime(codex_sessions / "old.jsonl", (old_time, old_time))

        with patch("handoff_relay._utils.Path.home", return_value=fake_home):
            result = detect_current_agent()

        assert result == "claude-code"

    def test_detects_codex(self, tmp_path: Path) -> None:
        """Most recent session in ~/.codex/sessions wins."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        claude_sessions = fake_home / ".claude" / "sessions"
        claude_sessions.mkdir(parents=True)
        (claude_sessions / "old.json").write_text("{}", encoding="utf-8")

        codex_sessions = fake_home / ".codex" / "sessions"
        codex_sessions.mkdir(parents=True)
        (codex_sessions / "new.jsonl").write_text("{}", encoding="utf-8")

        # Make claude file older
        old_time = 1000.0
        import os
        os.utime(claude_sessions / "old.json", (old_time, old_time))

        with patch("handoff_relay._utils.Path.home", return_value=fake_home):
            result = detect_current_agent()

        assert result == "codex-cli"

    def test_returns_none_when_no_sessions(self, tmp_path: Path) -> None:
        """No session directories → None."""
        fake_home = tmp_path / "home"
        fake_home.mkdir()

        with patch("handoff_relay._utils.Path.home", return_value=fake_home):
            result = detect_current_agent()

        assert result is None


# ── CodexAdapter ──────────────────────────────────────────────────────


class TestCodexAdapter:
    """CodexAdapter creation, injection, cleanup."""

    @pytest.fixture
    def adapter(self, tmp_path: Path) -> CodexAdapter:
        store = LocalHandoffStore(base_dir=tmp_path / "store")
        return CodexAdapter(store=store)

    @pytest.mark.asyncio
    async def test_create_package_with_messages(self, adapter: CodexAdapter, tmp_path: Path) -> None:
        """Codex session with messages → package uses message content."""
        session_dir = tmp_path / "codex_sessions"
        sessions = session_dir / "2024" / "01" / "15"
        sessions.mkdir(parents=True)
        session_file = sessions / "rollout.jsonl"

        lines = [
            json.dumps({"role": "user", "content": "Refactor utils"}),
            json.dumps({"role": "assistant", "content": "Done refactoring"}),
        ]
        session_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        from handoff_relay.adapters.session_parser import CodexSessionParser
        adapter._parser = CodexSessionParser(session_dir=session_dir)

        result = await adapter.create_package(
            task_id="refactor-task",
            reason=HandoffReason.USER_TRIGGERED,
            notes="Need tests",
        )

        assert "package_id" in result
        assert result["message_count"] == 2

    def test_inject_into_agents_md(self, adapter: CodexAdapter, project_dir: Path) -> None:
        """Injection adds handoff block to AGENTS.md."""
        path = adapter.inject_into_agents_md("pkg-123", project_dir)

        assert path == project_dir / "AGENTS.md"
        text = path.read_text(encoding="utf-8")
        assert "pkg-123" in text
        assert "<!-- handoff-inject-start -->" in text

    def test_cleanup_agents_md(self, adapter: CodexAdapter, project_dir: Path) -> None:
        """Cleanup removes handoff block."""
        adapter.inject_into_agents_md("pkg-456", project_dir)
        cleaned = adapter.cleanup_agents_md(project_dir)

        assert cleaned is True
        text = (project_dir / "AGENTS.md").read_text(encoding="utf-8")
        assert "pkg-456" not in text


# ── Service Switch ────────────────────────────────────────────────────


class TestSwitchCreatesHandoffPackage:
    """service.switch() creates packages and prepares target context."""

    @pytest.mark.asyncio
    async def test_switch_claude_to_codex(
        self,
        service: HandoffRelayService,
        project_dir: Path,
    ) -> None:
        """Switch from claude-code to codex-cli injects into AGENTS.md."""
        result = await service.switch(
            target_agent="codex-cli",
            task_id="auth-feature",
            notes="OAuth2 flow half done",
            project_dir=project_dir,
            source_agent="claude-code",
        )

        assert "error" not in result
        assert result["source_agent"] == "claude-code"
        assert result["target_agent"] == "codex-cli"
        assert result["task_id"] == "auth-feature"

        # Package was created
        pkg = await service.get_package(result["package_id"], format="full")
        assert "package" in pkg
        assert pkg["package"]["task"]["description"] == "OAuth2 flow half done"

        # Context injected into AGENTS.md
        assert result["injected_path"] == str(project_dir / "AGENTS.md")
        agents_text = (project_dir / "AGENTS.md").read_text(encoding="utf-8")
        assert result["package_id"] in agents_text

        # Launch command points to codex
        assert "codex" in result["launch_command"]

    @pytest.mark.asyncio
    async def test_switch_codex_to_claude(
        self,
        service: HandoffRelayService,
        project_dir: Path,
    ) -> None:
        """Switch from codex-cli to claude-code injects into CLAUDE.md."""
        result = await service.switch(
            target_agent="claude-code",
            task_id="bug-fix",
            notes="Race condition found in cache layer",
            project_dir=project_dir,
            source_agent="codex-cli",
        )

        assert "error" not in result
        assert result["source_agent"] == "codex-cli"
        assert result["target_agent"] == "claude-code"

        # Context injected into CLAUDE.md
        assert result["injected_path"] == str(project_dir / "CLAUDE.md")
        claude_text = (project_dir / "CLAUDE.md").read_text(encoding="utf-8")
        assert result["package_id"] in claude_text

        # Launch command points to claude
        assert "claude" in result["launch_command"]

    @pytest.mark.asyncio
    async def test_switch_same_agent_raises(self, service: HandoffRelayService) -> None:
        """Switching to same agent is rejected."""
        result = await service.switch(
            target_agent="claude-code",
            source_agent="claude-code",
        )

        assert "error" in result
        assert "same" in result["error"]

    @pytest.mark.asyncio
    async def test_switch_auto_task_id(self, service: HandoffRelayService) -> None:
        """Task ID is auto-generated when omitted."""
        result = await service.switch(
            target_agent="codex-cli",
            source_agent="claude-code",
        )

        assert "error" not in result
        assert result["task_id"].startswith("switch-")

    @pytest.mark.asyncio
    async def test_switch_opencode_generates_brief(
        self,
        service: HandoffRelayService,
        project_dir: Path,
    ) -> None:
        """Switch to opencode generates handoff-brief.md."""
        result = await service.switch(
            target_agent="opencode",
            task_id="test-task",
            notes="Test notes",
            project_dir=project_dir,
            source_agent="claude-code",
        )

        assert "error" not in result
        assert result["injected_path"] == str(project_dir / "handoff-brief.md")
        assert (project_dir / "handoff-brief.md").exists()

    @pytest.mark.asyncio
    async def test_switch_no_source_detected(self, service: HandoffRelayService) -> None:
        """No source_agent override and no sessions → error."""
        with patch("handoff_relay.service.detect_current_agent", return_value=None):
            result = await service.switch(target_agent="codex-cli")

        assert "error" in result
        assert "No active agent" in result["error"]


# ── Switch Marker ─────────────────────────────────────────────────────


class TestSwitchMarker:
    """write_switch_marker, read_and_clear_switch_marker, has_switch_marker."""

    def test_write_and_read_marker(self, tmp_path: Path) -> None:
        """Round-trip: write → read → clear."""
        from handoff_relay._utils import (
            write_switch_marker,
            read_and_clear_switch_marker,
            has_switch_marker,
        )

        fake_home = tmp_path / "home"
        marker = fake_home / ".handoff" / "switch_cmd"

        with patch("handoff_relay._utils._SWITCH_MARKER", marker):
            path = write_switch_marker("cd /proj && exec codex")
            assert path == marker
            assert marker.exists()
            assert has_switch_marker() is True

            cmd = read_and_clear_switch_marker()
            assert cmd == "cd /proj && exec codex"
            assert not marker.exists()
            assert has_switch_marker() is False

    def test_read_missing_returns_none(self, tmp_path: Path) -> None:
        """No marker → None."""
        from handoff_relay._utils import (
            read_and_clear_switch_marker,
            has_switch_marker,
        )

        fake_home = tmp_path / "home"
        marker = fake_home / ".handoff" / "switch_cmd"

        with patch("handoff_relay._utils._SWITCH_MARKER", marker):
            assert read_and_clear_switch_marker() is None
            assert has_switch_marker() is False


# ── Shell Hook Installer ──────────────────────────────────────────────


class TestInstallShellHook:
    """install-shell-hook CLI command for zsh and bash."""

    def test_install_zsh_hook(self, tmp_path: Path) -> None:
        """Hook block appended to .zshrc, idempotent on re-run."""
        from handoff_relay.cli import _ZSH_HOOK, _HOOK_START, _HOOK_END, _remove_hook_block

        zshrc = tmp_path / ".zshrc"
        zshrc.write_text("# existing config\n", encoding="utf-8")

        with patch("pathlib.Path.home", return_value=tmp_path):
            result = _remove_hook_block(zshrc.read_text(encoding="utf-8"))
            new_text = result.rstrip() + "\n\n" + _ZSH_HOOK + "\n"
            zshrc.write_text(new_text, encoding="utf-8")

        text = zshrc.read_text(encoding="utf-8")
        assert _HOOK_START in text
        assert _HOOK_END in text
        assert "handoff_precmd" in text
        assert "add-zsh-hook precmd" in text

        # Idempotent: remove and re-add
        text = _remove_hook_block(text)
        assert _HOOK_START not in text
        text = text.rstrip() + "\n\n" + _ZSH_HOOK + "\n"
        assert text.count(_HOOK_START) == 1

    def test_install_bash_hook(self, tmp_path: Path) -> None:
        """Hook block appended to .bashrc."""
        from handoff_relay.cli import _BASH_HOOK, _HOOK_START

        bashrc = tmp_path / ".bashrc"
        bashrc.write_text("export PATH=...\n", encoding="utf-8")

        with patch("pathlib.Path.home", return_value=tmp_path):
            from handoff_relay.cli import _remove_hook_block
            text = _remove_hook_block(bashrc.read_text(encoding="utf-8"))
            new_text = text.rstrip() + "\n\n" + _BASH_HOOK + "\n"
            bashrc.write_text(new_text, encoding="utf-8")

        text = bashrc.read_text(encoding="utf-8")
        assert _HOOK_START in text
        assert "__handoff_check" in text
        assert "PROMPT_COMMAND" in text

    def test_uninstall_removes_hook(self, tmp_path: Path) -> None:
        """Uninstall strips the hook block cleanly."""
        from handoff_relay.cli import _ZSH_HOOK, _remove_hook_block

        zshrc = tmp_path / ".zshrc"
        original = "# my zsh config\n"
        zshrc.write_text(original + "\n\n" + _ZSH_HOOK + "\n", encoding="utf-8")

        text = _remove_hook_block(zshrc.read_text(encoding="utf-8"))
        assert "handoff_precmd" not in text
        assert "# my zsh config" in text


# ── Tmux Detection & Switching ────────────────────────────────────────


class TestTmuxDetection:
    """is_inside_tmux detects the TMUX environment variable."""

    def test_inside_tmux_when_tmux_set(self) -> None:
        """TMUX env var present → True."""
        from handoff_relay._utils import is_inside_tmux

        with patch.dict("os.environ", {"TMUX": "/tmp/tmux-1000/default,1234,0"}):
            assert is_inside_tmux() is True

    def test_outside_tmux_when_tmux_missing(self) -> None:
        """TMUX env var absent → False."""
        from handoff_relay._utils import is_inside_tmux

        with patch.dict("os.environ", {}, clear=True):
            assert is_inside_tmux() is False


class TestTmuxSwitchLogic:
    """_agent_binary and _tmux_window_exists helpers."""

    def test_agent_binary_mapping(self) -> None:
        """Agent types map to correct CLI binaries."""
        from handoff_relay.cli import _agent_binary

        assert _agent_binary("claude-code") == "claude"
        assert _agent_binary("codex-cli") == "codex"
        assert _agent_binary("opencode") == "opencode"
        assert _agent_binary("unknown") == "unknown"

    def test_tmux_window_exists_true(self) -> None:
        """Window found in tmux list-windows output."""
        from handoff_relay.cli import _tmux_window_exists

        mock_result = type("R", (), {"returncode": 0, "stdout": "claude\ncodex\n", "stderr": ""})()
        with patch("subprocess.run", return_value=mock_result):
            assert _tmux_window_exists("codex") is True

    def test_tmux_window_exists_false(self) -> None:
        """Window not in tmux list-windows output."""
        from handoff_relay.cli import _tmux_window_exists

        mock_result = type("R", (), {"returncode": 0, "stdout": "claude\n", "stderr": ""})()
        with patch("subprocess.run", return_value=mock_result):
            assert _tmux_window_exists("codex") is False

    def test_tmux_window_exists_command_fails(self) -> None:
        """tmux list-windows fails → assume window does not exist."""
        from handoff_relay.cli import _tmux_window_exists

        mock_result = type("R", (), {"returncode": 1, "stdout": "", "stderr": "error"})()
        with patch("subprocess.run", return_value=mock_result):
            assert _tmux_window_exists("codex") is False

    def test_tmux_switch_kills_and_recreate(self, tmp_path: Path) -> None:
        """_tmux_switch kills existing window, creates new, selects it."""
        from handoff_relay.cli import _tmux_switch

        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> Any:
            calls.append(cmd)
            return type("R", (), {"returncode": 0})()

        with patch("handoff_relay.cli._tmux_window_exists", return_value=True):
            with patch("subprocess.run", side_effect=fake_run):
                with patch("time.sleep"):
                    _tmux_switch("codex-cli", tmp_path)

        # Should have called: kill-window, new-window, select-window
        assert len(calls) == 3
        assert calls[0] == ["tmux", "kill-window", "-t", "codex"]
        assert calls[1] == ["tmux", "new-window", "-n", "codex", "-c", str(tmp_path), "codex"]
        assert calls[2] == ["tmux", "select-window", "-t", "codex"]

    def test_tmux_switch_no_kill_when_missing(self, tmp_path: Path) -> None:
        """Window doesn't exist → skip kill, just create and select."""
        from handoff_relay.cli import _tmux_switch

        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> Any:
            calls.append(cmd)
            return type("R", (), {"returncode": 0})()

        with patch("handoff_relay.cli._tmux_window_exists", return_value=False):
            with patch("subprocess.run", side_effect=fake_run):
                with patch("time.sleep"):
                    _tmux_switch("claude-code", tmp_path)

        assert len(calls) == 2
        assert calls[0] == ["tmux", "new-window", "-n", "claude", "-c", str(tmp_path), "claude"]
        assert calls[1] == ["tmux", "select-window", "-t", "claude"]
