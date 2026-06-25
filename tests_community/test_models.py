"""Tests for core data models."""

import pytest
from agent_community.models import (
    AgentType,
    CommunityRunResult,
    ExecutionResult,
    SubTask,
    TaskPlan,
    TaskStatus,
)


class TestSubTask:
    def test_default_creation(self):
        st = SubTask(title="test")
        assert st.task_id.startswith("task-")
        assert st.status == TaskStatus.PENDING
        assert st.all_files == set()

    def test_all_files_combines_creates_and_modifies(self):
        st = SubTask(
            title="test",
            files_creates=["new.py"],
            files_modifies=["existing.py"],
        )
        assert st.all_files == {"new.py", "existing.py"}


class TestTaskPlan:
    def _make_plan(self) -> TaskPlan:
        t1 = SubTask(task_id="t1", title="Task 1", status=TaskStatus.PLANNED)
        t2 = SubTask(task_id="t2", title="Task 2", status=TaskStatus.PLANNED, depends_on=["t1"])
        t3 = SubTask(task_id="t3", title="Task 3", status=TaskStatus.PLANNED)
        return TaskPlan(subtasks=[t1, t2, t3])

    def test_get_ready_tasks_no_deps(self):
        plan = self._make_plan()
        ready = plan.get_ready_tasks()
        # t1 and t3 have no unmet deps; t2 depends on t1
        assert len(ready) == 2
        assert {t.task_id for t in ready} == {"t1", "t3"}

    def test_get_ready_tasks_after_completion(self):
        plan = self._make_plan()
        plan.subtasks[0].status = TaskStatus.COMPLETED  # t1 done
        ready = plan.get_ready_tasks()
        # Now t2 should also be ready
        assert len(ready) == 2
        assert {t.task_id for t in ready} == {"t2", "t3"}

    def test_all_completed(self):
        plan = self._make_plan()
        assert not plan.all_completed()
        for t in plan.subtasks:
            t.status = TaskStatus.COMPLETED
        assert plan.all_completed()

    def test_has_failures(self):
        plan = self._make_plan()
        assert not plan.has_failures()
        plan.subtasks[0].status = TaskStatus.FAILED
        assert plan.has_failures()

    def test_validate_file_ownership_no_conflict(self):
        t1 = SubTask(task_id="t1", files_creates=["a.py"], files_modifies=["b.py"])
        t2 = SubTask(task_id="t2", files_creates=["c.py"], files_modifies=["d.py"])
        plan = TaskPlan(subtasks=[t1, t2])
        assert plan.validate_file_ownership() == []

    def test_validate_file_ownership_with_conflict(self):
        t1 = SubTask(task_id="t1", files_creates=["a.py"], files_modifies=["shared.py"])
        t2 = SubTask(task_id="t2", files_creates=["b.py"], files_modifies=["shared.py"])
        plan = TaskPlan(subtasks=[t1, t2])
        conflicts = plan.validate_file_ownership()
        assert len(conflicts) == 1
        assert "shared.py" in conflicts[0]


class TestExecutionResult:
    def test_creation(self):
        r = ExecutionResult(
            task_id="t1",
            agent_type=AgentType.CLAUDE_CODE,
            success=True,
            output="done",
            cost_usd=0.05,
        )
        assert r.success
        assert r.cost_usd == 0.05
