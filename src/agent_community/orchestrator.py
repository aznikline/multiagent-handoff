"""CommunityOrchestrator — ties together planning, routing, execution, and merging.

This is the central controller for a multi-agent community run.
It coordinates the full lifecycle:

    1. Scout & Decompose  (planner/decomposer.py)
    2. Route to agents    (router/agent_registry.py)
    3. Create worktrees   (executor/worktree_mgr.py)
    4. Execute in DAG     (planner/dag.py)
    5. Merge branches     (merger/branch_merger.py)
    6. Validate           (merger/branch_merger.py)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_community.executor.process_pool import ProcessPool, get_runner
from agent_community.executor.worktree_mgr import WorktreeManager, WorktreeError
from agent_community.merger.branch_merger import BranchMerger
from agent_community.models import (
    AgentType,
    CommunityRunResult,
    ExecutionResult,
    SubTask,
    TaskPlan,
    TaskStatus,
)
from agent_community.observer.cost_tracker import CostTracker
from agent_community.planner.dag import DAGExecutor
from agent_community.planner.decomposer import TaskDecomposer
from agent_community.router.agent_registry import AgentRegistry

logger = logging.getLogger(__name__)


class CommunityOrchestrator:
    """Orchestrate a multi-agent community run."""

    def __init__(
        self,
        project_dir: Path | str,
        allowed_agents: list[AgentType] | None = None,
        max_concurrent: int = 3,
        max_turns: int = 20,
        timeout_seconds: int = 600,
        validate_command: str = "pytest -x -q",
        skip_validation: bool = False,
        dry_run: bool = False,
    ) -> None:
        self._project = Path(project_dir).resolve()
        self._allowed_agents = allowed_agents
        self._max_concurrent = max_concurrent
        self._max_turns = max_turns
        self._timeout = timeout_seconds
        self._validate_cmd = validate_command
        self._skip_validation = skip_validation
        self._dry_run = dry_run

        # Components
        self._worktree_mgr = WorktreeManager(self._project)
        self._decomposer = TaskDecomposer()
        self._registry = AgentRegistry()
        self._pool = ProcessPool(
            max_concurrent=max_concurrent,
            default_max_turns=max_turns,
            timeout_seconds=timeout_seconds,
        )
        self._merger = BranchMerger(self._project)
        self._cost_tracker = CostTracker()

        # Disable unavailable agents
        if allowed_agents:
            for agent_type in AgentType:
                if agent_type not in allowed_agents:
                    profile = self._registry._profiles.get(agent_type)
                    if profile:
                        profile.available = False

    async def run(self, task: str) -> CommunityRunResult:
        """Execute a full community run for a task.

        Args:
            task: The high-level task description.

        Returns:
            CommunityRunResult with full execution details.
        """
        result = CommunityRunResult(original_task=task)
        self._log("🚀 Starting community run", task)

        try:
            # Phase 1: Decompose
            self._log("📋 Phase 1: Decomposing task...")
            plan = await self._decomposer.decompose(task, self._project)
            result.plan = plan

            if not plan.suitable_for_parallel:
                self._log("⚠️  Task not suitable for parallel execution")
                self._log(f"   Reason: {plan.reason_if_not_suitable}")
                return result

            # Validate file ownership
            conflicts = plan.validate_file_ownership()
            if conflicts:
                self._log("⚠️  File ownership conflicts detected:")
                for c in conflicts:
                    self._log(f"   {c}")
                self._log("   Aborting parallel execution.")
                return result

            self._log(f"✓ Plan: {len(plan.subtasks)} sub-tasks")
            for st in plan.subtasks:
                deps = f" (depends: {st.depends_on})" if st.depends_on else ""
                self._log(f"   [{st.task_id}] {st.title}{deps}")
                self._log(f"     Creates: {st.files_creates}")
                self._log(f"     Modifies: {st.files_modifies}")

            # Phase 2: Route
            self._log("🔀 Phase 2: Routing tasks to agents...")
            routes = self._registry.route_all(plan.subtasks, self._allowed_agents)
            for subtask in plan.subtasks:
                subtask.assigned_agent = routes[subtask.task_id]
                self._log(
                    f"   {subtask.task_id} → {subtask.assigned_agent.value}"
                )

            if self._dry_run:
                self._log("🏁 Dry run complete (no execution)")
                return result

            # Phase 3: Create worktrees
            self._log("🌳 Phase 3: Creating worktrees...")
            self._worktree_mgr.ensure_git_repo()
            for subtask in plan.subtasks:
                try:
                    wt_path = self._worktree_mgr.create_worktree(
                        subtask.branch_name, subtask.task_id
                    )
                    subtask.worktree_path = wt_path
                    self._log(f"   {subtask.task_id} → {wt_path}")
                except WorktreeError as exc:
                    self._log(f"✗ Worktree creation failed for {subtask.task_id}: {exc}")
                    subtask.status = TaskStatus.FAILED
                    return result

            # Phase 4: Execute via DAG
            self._log("⚡ Phase 4: Executing tasks...")

            async def execute_task(subtask: SubTask) -> ExecutionResult:
                prompt = self._build_prompt(subtask, task)
                return await self._pool.execute_task(subtask, prompt)

            def on_complete(subtask: SubTask, exec_result: ExecutionResult) -> None:
                self._cost_tracker.record(
                    subtask.assigned_agent.value if subtask.assigned_agent else "unknown",
                    exec_result.cost_usd,
                    exec_result.duration_seconds,
                )
                status = "✓" if exec_result.success else "✗"
                self._log(
                    f"   {status} {subtask.task_id} "
                    f"(${exec_result.cost_usd:.4f}, {exec_result.duration_seconds:.1f}s)"
                )

            dag_executor = DAGExecutor(
                execute_fn=execute_task,
                on_task_complete=on_complete,
            )
            execution_results = await dag_executor.execute_plan(plan)
            result.execution_results = execution_results

            # Check for failures
            if plan.has_failures():
                failed = [t for t in plan.subtasks if t.status == TaskStatus.FAILED]
                self._log(f"⚠️  {len(failed)} task(s) failed, skipping merge")
                return result

            # Phase 5: Merge
            self._log("🔗 Phase 5: Merging branches...")
            branches = [st.branch_name for st in plan.subtasks]
            merge_ok, merge_conflicts = self._merger.merge_branches(branches)
            result.merge_success = merge_ok
            result.merge_conflicts = merge_conflicts

            if not merge_ok:
                self._log("⚠️  Merge conflicts:")
                for c in merge_conflicts:
                    self._log(f"   {c}")

            # Phase 6: Validate
            if merge_ok and not self._skip_validation:
                self._log("🧪 Phase 6: Validating...")
                passed, output = self._merger.validate_merge(self._validate_cmd)
                result.validation_passed = passed
                if passed:
                    self._log("✓ Validation passed")
                else:
                    self._log(f"✗ Validation failed:\n{output[:500]}")

        except Exception as exc:
            logger.exception("Community run failed")
            self._log(f"✗ Run failed: {exc}")
        finally:
            # Cleanup
            result.completed_at = datetime.now()
            result.total_cost_usd = self._cost_tracker.total_cost
            result.total_duration_seconds = self._cost_tracker.wall_clock
            self._log("")
            self._log(self._cost_tracker.summary())
            self._log("")
            self._log("🧹 Cleaning up worktrees...")
            self._worktree_mgr.cleanup_all()

        return result

    def _build_prompt(self, subtask: SubTask, original_task: str) -> str:
        """Build the agent prompt for a sub-task."""
        parts = [
            f"Task: {subtask.title}",
            f"",
            f"Instructions: {subtask.description}",
            f"",
        ]
        if subtask.files_creates:
            parts.append(f"Files to create: {', '.join(subtask.files_creates)}")
        if subtask.files_modifies:
            parts.append(f"Files to modify: {', '.join(subtask.files_modifies)}")
        parts.append(f"")
        parts.append(f"Context: This is part of a larger task: '{original_task}'")
        parts.append(f"You are working in an isolated worktree. Only modify the files listed above.")
        return "\n".join(parts)

    def _log(self, *args: Any) -> None:
        """Print a formatted log line."""
        msg = " ".join(str(a) for a in args)
        print(f"[community] {msg}")
        logger.info(msg)

    def cleanup(self) -> int:
        """Remove all community worktrees. Returns count removed."""
        return self._worktree_mgr.cleanup_all()
