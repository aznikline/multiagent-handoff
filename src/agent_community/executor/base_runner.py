"""Base runner interface for coding agents."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from agent_community.models import AgentType, ExecutionResult

logger = logging.getLogger(__name__)


class BaseRunner(ABC):
    """Abstract base class for agent runners.

    Each runner wraps a specific coding agent CLI and provides a uniform
    interface for executing tasks and collecting results.
    """

    def __init__(self, agent_type: AgentType) -> None:
        self._agent_type = agent_type

    @property
    def agent_type(self) -> AgentType:
        return self._agent_type

    @abstractmethod
    def build_command(
        self,
        prompt: str,
        work_dir: Path,
        max_turns: int = 20,
        allowed_tools: list[str] | None = None,
    ) -> list[str]:
        """Build the CLI command for this agent."""

    async def execute(
        self,
        task_id: str,
        prompt: str,
        work_dir: Path,
        max_turns: int = 20,
        allowed_tools: list[str] | None = None,
        timeout_seconds: int = 600,
    ) -> ExecutionResult:
        """Execute a task with this agent.

        Args:
            task_id: Task identifier.
            prompt: The task prompt/instructions.
            work_dir: Working directory (typically a worktree).
            max_turns: Maximum agent turns.
            allowed_tools: Tools the agent can use without confirmation.
            timeout_seconds: Maximum execution time.

        Returns:
            ExecutionResult with output, cost, and status.
        """
        cmd = self.build_command(prompt, work_dir, max_turns, allowed_tools)
        logger.info(
            "Starting %s for task %s in %s",
            self._agent_type.value,
            task_id,
            work_dir,
        )
        logger.debug("Command: %s", " ".join(cmd))

        start_time = time.monotonic()

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(work_dir),
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_seconds
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                duration = time.monotonic() - start_time
                return ExecutionResult(
                    task_id=task_id,
                    agent_type=self._agent_type,
                    success=False,
                    error=f"Timeout after {timeout_seconds}s",
                    duration_seconds=duration,
                )

            duration = time.monotonic() - start_time
            return self._parse_result(
                task_id=task_id,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                returncode=proc.returncode or 0,
                duration=duration,
            )

        except FileNotFoundError as exc:
            duration = time.monotonic() - start_time
            return ExecutionResult(
                task_id=task_id,
                agent_type=self._agent_type,
                success=False,
                error=f"Agent binary not found: {exc}",
                duration_seconds=duration,
            )
        except Exception as exc:
            duration = time.monotonic() - start_time
            return ExecutionResult(
                task_id=task_id,
                agent_type=self._agent_type,
                success=False,
                error=f"Unexpected error: {exc}",
                duration_seconds=duration,
            )

    @abstractmethod
    def _parse_result(
        self,
        task_id: str,
        stdout: str,
        stderr: str,
        returncode: int,
        duration: float,
    ) -> ExecutionResult:
        """Parse the raw CLI output into an ExecutionResult."""
