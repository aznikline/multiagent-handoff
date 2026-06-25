"""Tests for the CLI interface."""

import pytest
from typer.testing import CliRunner
from agent_community.cli import app


runner = CliRunner()


class TestCLI:
    def test_status_command(self):
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "Agent Community" in result.output
        assert "claude-code" in result.output
        assert "codex-cli" in result.output

    def test_cleanup_command(self, tmp_path):
        result = runner.invoke(app, ["cleanup", "--project-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "Cleaned up" in result.output

    def test_plan_requires_claude(self):
        # Plan command will try to call claude, which may not be available
        # Just verify the command exists and accepts arguments
        result = runner.invoke(app, ["plan", "--help"])
        assert result.exit_code == 0
        assert "Decompose" in result.output

    def test_run_help(self):
        result = runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        assert "parallel" in result.output.lower() or "execute" in result.output.lower()
