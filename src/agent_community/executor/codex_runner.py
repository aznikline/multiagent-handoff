"""Codex CLI runner — wraps `codex exec` for non-interactive execution."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agent_community.executor.base_runner import BaseRunner
from agent_community.models import AgentType, ExecutionResult

logger = logging.getLogger(__name__)


class CodexRunner(BaseRunner):
    """Execute tasks via Codex CLI's non-interactive mode.

    Uses ``codex exec --json`` for JSONL streaming output.
    """

    def __init__(self) -> None:
        super().__init__(AgentType.CODEX_CLI)

    def build_command(
        self,
        prompt: str,
        work_dir: Path,
        max_turns: int = 20,
        allowed_tools: list[str] | None = None,
    ) -> list[str]:
        cmd = [
            "codex", "exec",
            "--json",
            "--sandbox", "workspace-write",
            prompt,
        ]
        return cmd

    def _parse_result(
        self,
        task_id: str,
        stdout: str,
        stderr: str,
        returncode: int,
        duration: float,
    ) -> ExecutionResult:
        """Parse Codex CLI JSONL output.

        Codex outputs newline-delimited JSON events. We extract:
        - The last agent_message as the result text
        - Token usage from turn.completed events
        """
        messages: list[str] = []
        cost = 0.0
        total_input_tokens = 0
        total_output_tokens = 0

        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type", "")

            # Extract agent messages
            if event_type == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "agent_message":
                    text = item.get("text", "")
                    if text:
                        messages.append(text)

            # Extract usage
            elif event_type == "turn.completed":
                usage = event.get("usage", {})
                total_input_tokens += usage.get("input_tokens", 0)
                total_output_tokens += usage.get("output_tokens", 0)

        # Rough cost estimation (GPT-4o pricing)
        cost = (total_input_tokens * 2.5 + total_output_tokens * 10.0) / 1_000_000

        result_text = "\n".join(messages) if messages else stdout[:2000]
        success = returncode == 0

        if not success and not messages:
            result_text = f"Exit code {returncode}"
            if stderr:
                result_text += f"\nStderr: {stderr[:500]}"

        logger.info(
            "Codex CLI task %s: %s (tokens=%d+%d, cost=$%.4f, %.1fs)",
            task_id,
            "OK" if success else "FAILED",
            total_input_tokens,
            total_output_tokens,
            cost,
            duration,
        )

        return ExecutionResult(
            task_id=task_id,
            agent_type=AgentType.CODEX_CLI,
            success=success,
            output=result_text,
            error=stderr[:1000] if not success else "",
            cost_usd=cost,
            duration_seconds=duration,
            raw_json={"input_tokens": total_input_tokens, "output_tokens": total_output_tokens},
        )
