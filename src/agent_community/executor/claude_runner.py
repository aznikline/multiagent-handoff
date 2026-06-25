"""Claude Code runner — wraps `claude -p` for non-interactive execution."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agent_community.executor.base_runner import BaseRunner
from agent_community.models import AgentType, ExecutionResult

logger = logging.getLogger(__name__)


class ClaudeRunner(BaseRunner):
    """Execute tasks via Claude Code's non-interactive mode.

    Uses ``claude -p`` with ``--output-format json`` for structured output.
    """

    def __init__(self) -> None:
        super().__init__(AgentType.CLAUDE_CODE)

    def build_command(
        self,
        prompt: str,
        work_dir: Path,
        max_turns: int = 20,
        allowed_tools: list[str] | None = None,
    ) -> list[str]:
        cmd = [
            "claude",
            "-p", prompt,
            "--output-format", "json",
            "--max-turns", str(max_turns),
        ]

        if allowed_tools:
            cmd.extend(["--allowedTools", ",".join(allowed_tools)])
        else:
            # Default: allow common coding tools
            cmd.extend(["--allowedTools", "Read,Edit,Write,Bash,Glob,Grep"])

        return cmd

    def _parse_result(
        self,
        task_id: str,
        stdout: str,
        stderr: str,
        returncode: int,
        duration: float,
    ) -> ExecutionResult:
        """Parse Claude Code JSON output."""
        result_text = ""
        cost = 0.0
        session_id = None
        raw_json: dict[str, Any] = {}

        if stdout.strip():
            try:
                raw_json = json.loads(stdout.strip())
                result_text = raw_json.get("result", stdout)
                cost = raw_json.get("total_cost_usd", 0.0)
                session_id = raw_json.get("session_id")
            except json.JSONDecodeError:
                # Claude might output non-JSON in some cases
                result_text = stdout

        success = returncode == 0

        if not success and not result_text:
            result_text = f"Exit code {returncode}"
            if stderr:
                result_text += f"\nStderr: {stderr[:500]}"

        logger.info(
            "Claude Code task %s: %s (cost=$%.4f, %.1fs)",
            task_id,
            "OK" if success else "FAILED",
            cost,
            duration,
        )

        return ExecutionResult(
            task_id=task_id,
            agent_type=AgentType.CLAUDE_CODE,
            success=success,
            output=result_text,
            error=stderr[:1000] if not success else "",
            cost_usd=cost,
            duration_seconds=duration,
            session_id=session_id,
            raw_json=raw_json,
        )
