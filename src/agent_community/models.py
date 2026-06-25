"""Core data models for Agent Community orchestration."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


class TaskStatus(str, Enum):
    """Lifecycle states for a sub-task."""

    PENDING = "pending"
    PLANNED = "planned"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    MERGED = "merged"


class AgentType(str, Enum):
    """Supported coding agents."""

    CLAUDE_CODE = "claude-code"
    CODEX_CLI = "codex-cli"
    OPENCODE = "opencode"


@dataclass
class SubTask:
    """A single decomposed unit of work."""

    task_id: str = field(default_factory=lambda: f"task-{uuid.uuid4().hex[:8]}")
    title: str = ""
    description: str = ""
    files_creates: list[str] = field(default_factory=list)
    files_modifies: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)  # task_ids
    assigned_agent: AgentType | None = None
    status: TaskStatus = TaskStatus.PENDING
    branch_name: str = ""
    worktree_path: Path | None = None
    result_summary: str = ""
    cost_usd: float = 0.0
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def all_files(self) -> set[str]:
        return set(self.files_creates) | set(self.files_modifies)

    @property
    def is_ready(self) -> bool:
        """True if all dependencies are completed."""
        return self.status in (TaskStatus.PENDING, TaskStatus.PLANNED)


@dataclass
class TaskPlan:
    """A decomposed task plan with sub-tasks and dependency graph."""

    plan_id: str = field(default_factory=lambda: f"plan-{uuid.uuid4().hex[:8]}")
    original_task: str = ""
    subtasks: list[SubTask] = field(default_factory=list)
    suitable_for_parallel: bool = True
    reason_if_not_suitable: str = ""
    created_at: datetime = field(default_factory=datetime.now)

    def get_ready_tasks(self) -> list[SubTask]:
        """Return tasks whose dependencies are all completed."""
        completed_ids = {
            t.task_id for t in self.subtasks if t.status == TaskStatus.COMPLETED
        }
        return [
            t
            for t in self.subtasks
            if t.status in (TaskStatus.PENDING, TaskStatus.PLANNED)
            and all(dep in completed_ids for dep in t.depends_on)
        ]

    def all_completed(self) -> bool:
        return all(
            t.status in (TaskStatus.COMPLETED, TaskStatus.MERGED)
            for t in self.subtasks
        )

    def has_failures(self) -> bool:
        return any(t.status == TaskStatus.FAILED for t in self.subtasks)

    def validate_file_ownership(self) -> list[str]:
        """Check that no file is owned by multiple tasks."""
        file_owners: dict[str, list[str]] = {}
        for task in self.subtasks:
            for f in task.all_files:
                file_owners.setdefault(f, []).append(task.task_id)

        conflicts = []
        for filepath, owners in file_owners.items():
            if len(owners) > 1:
                conflicts.append(
                    f"File '{filepath}' claimed by multiple tasks: {owners}"
                )
        return conflicts


@dataclass
class ExecutionResult:
    """Result from a single agent execution."""

    task_id: str
    agent_type: AgentType
    success: bool
    output: str = ""
    error: str = ""
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    session_id: str | None = None
    files_changed: list[str] = field(default_factory=list)
    raw_json: dict[str, Any] = field(default_factory=dict)


@dataclass
class CommunityRunResult:
    """Final result of a full community run."""

    run_id: str = field(default_factory=lambda: f"run-{uuid.uuid4().hex[:8]}")
    original_task: str = ""
    plan: TaskPlan | None = None
    execution_results: list[ExecutionResult] = field(default_factory=list)
    merge_success: bool = False
    merge_conflicts: list[str] = field(default_factory=list)
    validation_passed: bool | None = None
    total_cost_usd: float = 0.0
    total_duration_seconds: float = 0.0
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
