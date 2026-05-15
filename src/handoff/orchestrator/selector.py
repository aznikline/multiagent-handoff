"""Target agent selection strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentDescriptor:
    """Descriptor for an available agent in the pool."""

    agent_id: str
    capabilities: frozenset[str]
    current_load: int = 0  # Number of active tasks
    max_concurrency: int = 1
    token_window_remaining: int = 0  # Approximate remaining context window
    framework: str = "custom"
    version: str = "1.0.0"
    accepts_handoff: bool = True


class AgentSelector(ABC):
    """Abstract strategy for selecting a target agent."""

    @abstractmethod
    async def select(
        self,
        candidates: list[AgentDescriptor],
        required_capabilities: list[str],
        priority: str,
    ) -> AgentDescriptor | None:
        """Select the best target agent from the candidate pool.

        Args:
            candidates: Available agents.
            required_capabilities: Capabilities the task requires.
            priority: Task priority level.

        Returns:
            Selected agent descriptor, or None if no suitable agent found.
        """
        raise NotImplementedError


class CapabilityBasedSelector(AgentSelector):
    """Selects agents based on capability match and load balancing.

    Selection algorithm:
    1. Filter to agents that accept handoffs.
    2. Filter to agents with all required capabilities.
    3. Filter to agents with available concurrency.
    4. Score remaining candidates by: token_window + (max_load - current_load)*weight.
    5. Return highest scorer.
    """

    def __init__(self, load_weight: float = 100.0) -> None:
        self.load_weight = load_weight

    async def select(
        self,
        candidates: list[AgentDescriptor],
        required_capabilities: list[str],
        priority: str,
    ) -> AgentDescriptor | None:
        required = set(required_capabilities)

        eligible = [
            agent for agent in candidates
            if agent.accepts_handoff
            and required.issubset(agent.capabilities)
            and agent.current_load < agent.max_concurrency
        ]

        if not eligible:
            return None

        # Score and pick best
        def score(agent: AgentDescriptor) -> float:
            load_score = (agent.max_concurrency - agent.current_load) * self.load_weight
            token_score = agent.token_window_remaining
            return load_score + token_score

        return max(eligible, key=score)
