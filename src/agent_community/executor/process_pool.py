"""Process pool for managing concurrent agent executions."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from agent_community.executor.base_runner import BaseRunner
from agent_community.executor.claude_runner import ClaudeRunner
from agent_community.executor.codex_runner import CodexRunner
from agent_community.models import AgentType, ExecutionResult, SubTask

logger = logging.getLogger(__name__)

# Map agent types to runner classes
_RUNNER_MAP: dict[AgentType, type[BaseRunner]] = {
    AgentType.CLAUDE_CODE: ClaudeRunner,
    AgentType.CODEX_CLI: CodexRunner,
}


def get_runner(agent_type: AgentType) -> BaseRunner:
    """Get a runner instance for the given agent type."""
    if agent_type == AgentType.CLAUDE_CODE:
        return ClaudeRunner()
    elif agent_type == AgentType.CODEX_CLI:
        return CodexRunner()
    else:
        raise ValueError(f"No runner for agent type: {agent_type}")


class ProcessPool:
    """Manage concurrent execution of multiple agent tasks.

    Respects per-agent concurrency limits and handles dependency ordering.
    """

    def __init__(
        self,
        max_concurrent: int = 3,
        default_max_turns: int = 20,
        timeout_seconds: int = 600,
    ) -> None:
        self._max_concurrent = max_concurrent
        self._default_max_turns = default_max_turns
        self._timeout = timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._results: dict[str, ExecutionResult] = {}

    async def execute_task(self, task: SubTask, prompt: str) -> ExecutionResult:
        """Execute a single task, respecting concurrency limits."""
        if task.assigned_agent is None:
            raise ValueError(f"Task {task.task_id} has no assigned agent")
        if task.worktree_path is None:
            raise ValueError(f"Task {task.task_id} has no worktree path")

        runner = get_runner(task.assigned_agent)

        async with self._semaphore:
            result = await runner.execute(
                task_id=task.task_id,
                prompt=prompt,
                work_dir=task.worktree_path,
                max_turns=self._default_max_turns,
                timeout_seconds=self._timeout,
            )

        self._results[task.task_id] = result
        return result

    async def execute_batch(
        self, tasks: list[SubTask], prompts: dict[str, str]
    ) -> list[ExecutionResult]:
        """Execute multiple tasks concurrently.

        Args:
            tasks: List of tasks to execute.
            prompts: Mapping of task_id -> prompt text.

        Returns:
            List of ExecutionResults (one per task).
        """
        coros = []
        for task in tasks:
            prompt = prompts.get(task.task_id, task.description)
            coros.append(self.execute_task(task, prompt))

        results = await asyncio.gather(*coros, return_exceptions=True)

        execution_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                execution_results.append(
                    ExecutionResult(
                        task_id=tasks[i].task_id,
                        agent_type=tasks[i].assigned_agent or AgentType.CLAUDE_CODE,
                        success=False,
                        error=str(result),
                    )
                )
            else:
                execution_results.append(result)

        return execution_results

    def get_result(self, task_id: str) -> ExecutionResult | None:
        return self._results.get(task_id)

    @property
    def all_results(self) -> dict[str, ExecutionResult]:
        return dict(self._results)
