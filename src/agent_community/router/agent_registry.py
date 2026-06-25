"""Agent capability registry and task-to-agent routing.

Each agent has a profile describing its strengths, weaknesses, cost,
and concurrency limits. The router matches sub-tasks to the best agent
based on task characteristics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from agent_community.models import AgentType, SubTask

logger = logging.getLogger(__name__)


@dataclass
class AgentProfile:
    """Capability profile for a coding agent."""

    agent_type: AgentType
    binary_name: str
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    max_concurrent: int = 3
    cost_per_1k_tokens: float = 0.01
    max_turns: int = 20
    available: bool = True

    def suitability_score(self, task: SubTask) -> float:
        """Score how suitable this agent is for a task (0.0 - 1.0)."""
        score = 0.5  # base score

        # Analyze task description for capability keywords
        task_text = (task.title + " " + task.description).lower()

        # Strength matching
        strength_keywords = {
            "complex_refactoring": ["refactor", "restructure", "rewrite", "redesign", "migrate"],
            "architecture_design": ["architecture", "design", "pattern", "structure", "module"],
            "documentation": ["doc", "readme", "comment", "explain", "guide"],
            "security_review": ["security", "auth", "permission", "encrypt", "vulnerability"],
            "rapid_prototyping": ["prototype", "scaffold", "bootstrap", "quick", "simple"],
            "test_writing": ["test", "spec", "assert", "coverage", "mock"],
            "bug_fixing": ["fix", "bug", "error", "issue", "patch", "debug"],
            "simple_crud": ["crud", "endpoint", "route", "api", "model"],
            "go_projects": ["go", "golang", "goroutine"],
            "small_tasks": ["small", "minor", "tweak", "update", "change"],
        }

        for capability in self.strengths:
            keywords = strength_keywords.get(capability, [])
            for kw in keywords:
                if kw in task_text:
                    score += 0.15

        for capability in self.weaknesses:
            keywords = strength_keywords.get(capability, [])
            for kw in keywords:
                if kw in task_text:
                    score -= 0.15

        # Cost factor — prefer cheaper agents for simple tasks
        file_count = len(task.files_creates) + len(task.files_modifies)
        if file_count <= 2 and self.cost_per_1k_tokens < 0.01:
            score += 0.1  # bonus for cheap agent on small task

        return max(0.0, min(1.0, score))


# Default agent profiles
DEFAULT_PROFILES: dict[AgentType, AgentProfile] = {
    AgentType.CLAUDE_CODE: AgentProfile(
        agent_type=AgentType.CLAUDE_CODE,
        binary_name="claude",
        strengths=[
            "complex_refactoring",
            "architecture_design",
            "documentation",
            "security_review",
        ],
        weaknesses=["speed", "cost"],
        max_concurrent=3,
        cost_per_1k_tokens=0.015,
        max_turns=25,
    ),
    AgentType.CODEX_CLI: AgentProfile(
        agent_type=AgentType.CODEX_CLI,
        binary_name="codex",
        strengths=[
            "rapid_prototyping",
            "test_writing",
            "bug_fixing",
            "simple_crud",
        ],
        weaknesses=["complex_architecture"],
        max_concurrent=5,
        cost_per_1k_tokens=0.005,
        max_turns=20,
    ),
}


class AgentRegistry:
    """Registry of available agents and their capability profiles."""

    def __init__(
        self,
        profiles: dict[AgentType, AgentProfile] | None = None,
    ) -> None:
        self._profiles = dict(profiles or DEFAULT_PROFILES)

    def register(self, profile: AgentProfile) -> None:
        """Register or update an agent profile."""
        self._profiles[profile.agent_type] = profile

    def available_agents(self) -> list[AgentProfile]:
        """Return all available agent profiles."""
        return [p for p in self._profiles.values() if p.available]

    def route_task(
        self,
        task: SubTask,
        allowed_agents: list[AgentType] | None = None,
    ) -> AgentType:
        """Select the best agent for a task.

        Args:
            task: The sub-task to route.
            allowed_agents: Optional list of allowed agent types.
                If None, all registered agents are considered.

        Returns:
            The best matching AgentType.

        Raises:
            ValueError: If no agents are available.
        """
        candidates = self.available_agents()

        if allowed_agents:
            candidates = [c for c in candidates if c.agent_type in allowed_agents]

        if not candidates:
            raise ValueError("No available agents for routing")

        # Score each candidate
        scored = [
            (profile.suitability_score(task), profile.agent_type)
            for profile in candidates
        ]

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)

        best_score, best_agent = scored[0]
        logger.info(
            "Routed task %s to %s (score=%.2f)",
            task.task_id,
            best_agent.value,
            best_score,
        )
        return best_agent

    def route_all(
        self,
        tasks: list[SubTask],
        allowed_agents: list[AgentType] | None = None,
    ) -> dict[str, AgentType]:
        """Route all tasks, returning a task_id -> agent_type mapping."""
        return {
            task.task_id: self.route_task(task, allowed_agents)
            for task in tasks
        }
