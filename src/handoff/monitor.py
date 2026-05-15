"""Handoff monitoring and automatic triggering.

Monitors agent runtime metrics (token usage, error rates) and
automatically initiates handoff when thresholds are breached.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from handoff.models.package import ContextPackage
from handoff.models.task import HandoffReason
from handoff.orchestrator.orchestrator import HandoffOrchestrator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TokenThreshold:
    """Configuration for token-based handoff triggering."""

    max_tokens: int = 6000
    warning_threshold: float = 0.8
    """Fraction of max_tokens at which to log a warning."""
    critical_threshold: float = 0.95
    """Fraction of max_tokens at which to trigger handoff."""

    def should_warn(self, current_tokens: int) -> bool:
        return current_tokens >= self.max_tokens * self.warning_threshold

    def should_trigger(self, current_tokens: int) -> bool:
        return current_tokens >= self.max_tokens * self.critical_threshold


class HandoffMonitor:
    """Monitors agent health and triggers automatic handoffs.

    Attach to an agent runner or framework middleware to observe
    token counts and error states in real time.
    """

    def __init__(
        self,
        orchestrator: HandoffOrchestrator,
        token_threshold: TokenThreshold | None = None,
        on_trigger: Callable[[str, HandoffReason, ContextPackage], Any] | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.token_threshold = token_threshold or TokenThreshold()
        self.on_trigger = on_trigger
        self._agent_token_counts: dict[str, int] = {}

    def record_token_usage(self, agent_id: str, token_count: int) -> None:
        """Record the current token count for an agent.

        Args:
            agent_id: Identifier of the agent being monitored.
            token_count: Current total token usage (prompt + completion).
        """
        self._agent_token_counts[agent_id] = token_count

        if self.token_threshold.should_warn(token_count):
            logger.warning(
                "Agent %s token usage at %.0f%% (%d / %d)",
                agent_id,
                token_count / self.token_threshold.max_tokens * 100,
                token_count,
                self.token_threshold.max_tokens,
            )

        if self.token_threshold.should_trigger(token_count):
            logger.critical(
                "Agent %s token threshold breached — handoff required",
                agent_id,
            )

    async def check_and_trigger(
        self,
        agent_id: str,
        package: ContextPackage,
        candidates: list[Any] | None = None,
    ) -> Any | None:
        """Check if the agent should hand off and trigger if needed.

        Returns:
            HandoffResult if triggered, None otherwise.
        """
        token_count = self._agent_token_counts.get(agent_id, 0)
        if not self.token_threshold.should_trigger(token_count):
            return None

        logger.info("Auto-triggering handoff for %s (tokens: %d)", agent_id, token_count)

        result = await self.orchestrator.initiate(
            source_agent_id=agent_id,
            reason=HandoffReason.TOKEN_LIMIT,
            package=package,
            candidates=candidates,
        )

        if self.on_trigger:
            try:
                self.on_trigger(agent_id, HandoffReason.TOKEN_LIMIT, package)
            except Exception:
                logger.exception("Trigger callback failed for %s", agent_id)

        return result

    def get_usage(self, agent_id: str) -> int:
        """Return the last recorded token count for an agent."""
        return self._agent_token_counts.get(agent_id, 0)

    def reset(self, agent_id: str) -> None:
        """Reset token tracking for an agent (e.g., after handoff)."""
        self._agent_token_counts.pop(agent_id, None)
