"""Tests for the DAG executor."""

import asyncio
import pytest
from agent_community.models import AgentType, ExecutionResult, SubTask, TaskPlan, TaskStatus
from agent_community.planner.dag import DAGExecutor


class TestDAGExecutor:
    @pytest.fixture
    def simple_plan(self) -> TaskPlan:
        t1 = SubTask(
            task_id="t1", title="Task 1",
            assigned_agent=AgentType.CLAUDE_CODE,
            status=TaskStatus.PLANNED,
        )
        t2 = SubTask(
            task_id="t2", title="Task 2",
            assigned_agent=AgentType.CODEX_CLI,
            status=TaskStatus.PLANNED,
            depends_on=["t1"],
        )
        t3 = SubTask(
            task_id="t3", title="Task 3",
            assigned_agent=AgentType.CLAUDE_CODE,
            status=TaskStatus.PLANNED,
        )
        return TaskPlan(subtasks=[t1, t2, t3])

    @pytest.mark.asyncio
    async def test_execute_plan_parallel(self, simple_plan):
        """Tasks without dependencies should run in parallel."""
        execution_order = []

        async def mock_execute(task: SubTask) -> ExecutionResult:
            execution_order.append(task.task_id)
            await asyncio.sleep(0.01)  # simulate work
            return ExecutionResult(
                task_id=task.task_id,
                agent_type=task.assigned_agent or AgentType.CLAUDE_CODE,
                success=True,
                output=f"Done: {task.title}",
                cost_usd=0.01,
            )

        dag = DAGExecutor(execute_fn=mock_execute)
        results = await dag.execute_plan(simple_plan)

        assert len(results) == 3
        assert all(r.success for r in results)
        # t1 and t3 should execute before t2 (t2 depends on t1)
        assert execution_order.index("t2") > execution_order.index("t1")

    @pytest.mark.asyncio
    async def test_execute_plan_failure_blocks_dependents(self, simple_plan):
        """Failed tasks should block their dependents."""
        async def mock_execute(task: SubTask) -> ExecutionResult:
            if task.task_id == "t1":
                return ExecutionResult(
                    task_id=task.task_id,
                    agent_type=AgentType.CLAUDE_CODE,
                    success=False,
                    error="Simulated failure",
                )
            return ExecutionResult(
                task_id=task.task_id,
                agent_type=task.assigned_agent or AgentType.CLAUDE_CODE,
                success=True,
                output="Done",
            )

        dag = DAGExecutor(execute_fn=mock_execute)
        results = await dag.execute_plan(simple_plan)

        # t1 failed, t2 (depends on t1) should also be marked failed
        t1 = next(t for t in simple_plan.subtasks if t.task_id == "t1")
        t2 = next(t for t in simple_plan.subtasks if t.task_id == "t2")
        assert t1.status == TaskStatus.FAILED
        assert t2.status == TaskStatus.FAILED
        # t3 should have completed successfully
        t3 = next(t for t in simple_plan.subtasks if t.task_id == "t3")
        assert t3.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_on_complete_callback(self, simple_plan):
        """on_task_complete callback should fire for each task."""
        callbacks = []

        async def mock_execute(task: SubTask) -> ExecutionResult:
            return ExecutionResult(
                task_id=task.task_id,
                agent_type=task.assigned_agent or AgentType.CLAUDE_CODE,
                success=True,
                cost_usd=0.01,
            )

        def on_complete(task, result):
            callbacks.append((task.task_id, result.success))

        dag = DAGExecutor(execute_fn=mock_execute, on_task_complete=on_complete)
        await dag.execute_plan(simple_plan)

        assert len(callbacks) == 3
        assert all(success for _, success in callbacks)
