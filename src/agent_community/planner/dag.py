"""DAG executor — run tasks respecting dependency ordering.

Implements a simple topological execution strategy:
1. Find all tasks with no unmet dependencies (ready tasks)
2. Execute ready tasks in parallel
3. On completion, check if new tasks became ready
4. Repeat until all tasks complete or a failure blocks progress
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from agent_community.models import (
    ExecutionResult,
    SubTask,
    TaskPlan,
    TaskStatus,
)

logger = logging.getLogger(__name__)


class DAGExecutor:
    """Execute a TaskPlan respecting dependency ordering.

    Tasks with no dependencies run in parallel. Tasks with dependencies
    wait until all their dependencies are completed.
    """

    def __init__(
        self,
        execute_fn: Callable[[SubTask], Awaitable[ExecutionResult]],
        on_task_complete: Callable[[SubTask, ExecutionResult], None] | None = None,
    ) -> None:
        """
        Args:
            execute_fn: Async function that executes a single task.
            on_task_complete: Optional callback when a task completes.
        """
        self._execute_fn = execute_fn
        self._on_complete = on_task_complete
        self._results: dict[str, ExecutionResult] = {}

    async def execute_plan(self, plan: TaskPlan) -> list[ExecutionResult]:
        """Execute all tasks in the plan respecting dependencies.

        Returns:
            List of all ExecutionResults.
        """
        all_results: list[ExecutionResult] = []

        while not plan.all_completed():
            # Find ready tasks
            ready = plan.get_ready_tasks()

            if not ready:
                if plan.has_failures():
                    # Some tasks failed, blocking remaining tasks
                    pending = [
                        t for t in plan.subtasks
                        if t.status in (TaskStatus.PENDING, TaskStatus.PLANNED)
                    ]
                    for t in pending:
                        t.status = TaskStatus.FAILED
                        logger.warning(
                            "Task %s cancelled due to upstream failure", t.task_id
                        )
                    break
                else:
                    # No ready tasks and no failures — shouldn't happen
                    logger.error("DAG deadlock: no ready tasks but not all completed")
                    break

            logger.info(
                "Executing %d ready task(s): %s",
                len(ready),
                [t.task_id for t in ready],
            )

            # Mark as running
            for task in ready:
                task.status = TaskStatus.RUNNING

            # Execute in parallel
            tasks_coros = [self._run_task(task) for task in ready]
            results = await asyncio.gather(*tasks_coros)

            for task, result in zip(ready, results):
                all_results.append(result)
                self._results[task.task_id] = result

                if result.success:
                    task.status = TaskStatus.COMPLETED
                    task.result_summary = result.output[:500]
                    task.cost_usd = result.cost_usd
                    logger.info("Task %s completed ($%.4f)", task.task_id, result.cost_usd)
                else:
                    task.status = TaskStatus.FAILED
                    task.result_summary = f"FAILED: {result.error[:200]}"
                    logger.warning("Task %s failed: %s", task.task_id, result.error[:200])

                if self._on_complete:
                    self._on_complete(task, result)

        return all_results

    async def _run_task(self, task: SubTask) -> ExecutionResult:
        """Execute a single task with error handling."""
        from agent_community.models import AgentType as _AT
        try:
            from datetime import datetime
            task.started_at = datetime.now()
            result = await self._execute_fn(task)
            task.completed_at = datetime.now()
            return result
        except Exception as exc:
            return ExecutionResult(
                task_id=task.task_id,
                agent_type=task.assigned_agent or _AT.CLAUDE_CODE,
                success=False,
                error=f"Execution error: {exc}",
            )

    @property
    def results(self) -> dict[str, ExecutionResult]:
        return dict(self._results)
