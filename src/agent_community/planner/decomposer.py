"""Task decomposition — break a high-level task into parallel sub-tasks.

Uses an LLM (via claude -p) to analyze the codebase and decompose the
user's task into a plan of sub-tasks with file ownership assignments.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from agent_community.models import SubTask, TaskPlan, TaskStatus

logger = logging.getLogger(__name__)

# Prompt that instructs the LLM to decompose tasks
_DECOMPOSE_SYSTEM_PROMPT = """You are a task decomposition engine for a multi-agent coding system.

Given a high-level coding task and information about the project structure, you must:
1. Analyze which files need to be created or modified
2. Break the task into 2-5 independent sub-tasks
3. Assign file ownership — each file belongs to exactly ONE sub-task
4. Define dependencies between sub-tasks

CRITICAL RULES:
- Files must not overlap between sub-tasks (disjoint ownership)
- If the task cannot be cleanly split without file conflicts, set suitable_for_parallel=false
- Each sub-task should be self-contained enough for an AI coding agent to complete independently
- Dependencies should be minimal — prefer parallel execution

You MUST respond with valid JSON only. No markdown, no explanation outside JSON.
"""

_DECOMPOSE_USER_PROMPT = """Project directory: {project_dir}

Project structure (top-level files):
{project_structure}

Task to decompose:
{task}

Respond with this exact JSON structure:
{{
  "suitable_for_parallel": true/false,
  "reason_if_not_suitable": "...",
  "subtasks": [
    {{
      "title": "Short title",
      "description": "Detailed instructions for the agent",
      "files_creates": ["new/file1.py", "new/file2.py"],
      "files_modifies": ["existing/file.py"],
      "depends_on": []  // indices of subtasks this depends on (0-indexed)
    }}
  ]
}}
"""


class TaskDecomposer:
    """Decompose a high-level task into parallel sub-tasks using an LLM."""

    def __init__(
        self,
        planner_agent: str = "claude-code",
        max_subtasks: int = 5,
    ) -> None:
        self._planner_agent = planner_agent
        self._max_subtasks = max_subtasks

    async def decompose(
        self,
        task: str,
        project_dir: Path,
    ) -> TaskPlan:
        """Decompose a task into sub-tasks.

        Args:
            task: The high-level task description.
            project_dir: Path to the project directory.

        Returns:
            TaskPlan with sub-tasks and dependency information.
        """
        # Get project structure for context
        structure = self._get_project_structure(project_dir)

        # Build the prompt
        user_prompt = _DECOMPOSE_USER_PROMPT.format(
            project_dir=str(project_dir),
            project_structure=structure,
            task=task,
        )

        # Call LLM for decomposition
        raw_response = await self._call_llm(user_prompt, project_dir)

        # Parse the response
        plan = self._parse_response(raw_response, task)
        return plan

    def _get_project_structure(self, project_dir: Path) -> str:
        """Get a simplified project structure listing."""
        try:
            result = subprocess.run(
                ["find", str(project_dir), "-maxdepth", "3",
                 "-not", "-path", "*/.git/*",
                 "-not", "-path", "*/node_modules/*",
                 "-not", "-path", "*/__pycache__/*",
                 "-not", "-path", "*/.venv/*",
                 "-not", "-path", "*/.community-worktrees/*",
                 "-type", "f"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            files = result.stdout.strip().splitlines()
            # Limit to 100 files to keep prompt manageable
            if len(files) > 100:
                files = files[:100]
                files.append(f"... and more (total {len(result.stdout.splitlines())} files)")

            # Make paths relative
            prefix = str(project_dir) + "/"
            return "\n".join(f.replace(prefix, "") for f in files)
        except Exception:
            return "(could not list project structure)"

    async def _call_llm(self, prompt: str, work_dir: Path) -> str:
        """Call the planner LLM via claude -p.
        
        The planner receives the project structure in the prompt, so it
        does not need any tools. We disable all tools to prevent the LLM
        from wasting turns on file reads.
        """
        # Combine system prompt into user prompt since --bare skips CLAUDE.md
        full_prompt = f"{_DECOMPOSE_SYSTEM_PROMPT}\n\n---\n\n{prompt}"

        cmd = [
            "claude", "-p", full_prompt,
            "--output-format", "text",
            "--max-turns", "1",
            "--bare",
            "--tools", "",
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(work_dir),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            raw_text = stdout.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace")[:500]
                logger.error("Planner LLM failed (rc=%d): %s", proc.returncode, err_msg)
                logger.debug("Raw stdout: %s", raw_text[:500])
                return ""

            if not raw_text:
                logger.warning("Planner LLM returned empty output")
                return ""

            return raw_text

        except asyncio.TimeoutError:
            logger.error("Planner LLM timed out after 120s")
            try:
                proc.kill()
            except Exception:
                pass
            return ""
        except Exception as exc:
            logger.error("Planner LLM error: %s", exc)
            return ""

    def _parse_response(self, response: str, original_task: str) -> TaskPlan:
        """Parse the LLM's JSON response into a TaskPlan."""
        plan = TaskPlan(original_task=original_task)

        if not response:
            plan.suitable_for_parallel = False
            plan.reason_if_not_suitable = "LLM planner returned empty response"
            return plan

        # Try to extract JSON from response (might have markdown wrapping)
        json_str = response.strip()
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0]
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0]

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            plan.suitable_for_parallel = False
            plan.reason_if_not_suitable = f"Failed to parse planner output as JSON: {json_str[:200]}"
            return plan

        plan.suitable_for_parallel = data.get("suitable_for_parallel", True)
        plan.reason_if_not_suitable = data.get("reason_if_not_suitable", "")

        if not plan.suitable_for_parallel:
            return plan

        # Parse sub-tasks
        raw_subtasks = data.get("subtasks", [])
        if not raw_subtasks:
            plan.suitable_for_parallel = False
            plan.reason_if_not_suitable = "Planner produced no sub-tasks"
            return plan

        if len(raw_subtasks) > self._max_subtasks:
            raw_subtasks = raw_subtasks[:self._max_subtasks]

        for i, raw in enumerate(raw_subtasks):
            subtask = SubTask(
                title=raw.get("title", f"Task {i+1}"),
                description=raw.get("description", ""),
                files_creates=raw.get("files_creates", []),
                files_modifies=raw.get("files_modifies", []),
                status=TaskStatus.PLANNED,
            )

            # Resolve dependency indices to task_ids
            deps = raw.get("depends_on", [])
            if deps and isinstance(deps, list):
                # We'll resolve these after all tasks are created
                subtask.depends_on = [f"_idx_{d}" for d in deps if isinstance(d, int)]

            plan.subtasks.append(subtask)

        # Resolve index-based dependencies to actual task_ids
        for subtask in plan.subtasks:
            resolved_deps = []
            for dep in subtask.depends_on:
                if dep.startswith("_idx_"):
                    idx = int(dep[5:])
                    if 0 <= idx < len(plan.subtasks):
                        resolved_deps.append(plan.subtasks[idx].task_id)
                else:
                    resolved_deps.append(dep)
            subtask.depends_on = resolved_deps

        # Generate branch names
        for i, subtask in enumerate(plan.subtasks):
            safe_title = subtask.title.lower().replace(" ", "-")[:30]
            safe_title = "".join(c for c in safe_title if c.isalnum() or c == "-")
            subtask.branch_name = f"community/task-{i+1}-{safe_title}"

        return plan
