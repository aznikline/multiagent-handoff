"""Real-time cost tracking for multi-agent runs."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class CostTracker:
    """Track token usage and costs across agents."""

    agent_costs: dict[str, float] = field(default_factory=dict)
    agent_durations: dict[str, float] = field(default_factory=dict)
    _start_time: float = field(default_factory=time.monotonic)

    def record(self, agent_type: str, cost_usd: float, duration: float) -> None:
        """Record cost for an agent execution."""
        self.agent_costs[agent_type] = self.agent_costs.get(agent_type, 0.0) + cost_usd
        self.agent_durations[agent_type] = self.agent_durations.get(agent_type, 0.0) + duration

    @property
    def total_cost(self) -> float:
        return sum(self.agent_costs.values())

    @property
    def total_duration(self) -> float:
        return time.monotonic() - self._start_time

    @property
    def wall_clock(self) -> float:
        return self.total_duration

    def summary(self) -> str:
        lines = ["Cost Summary:"]
        for agent, cost in sorted(self.agent_costs.items()):
            dur = self.agent_durations.get(agent, 0)
            lines.append(f"  {agent}: ${cost:.4f} ({dur:.1f}s)")
        lines.append(f"  Total: ${self.total_cost:.4f} (wall: {self.wall_clock:.1f}s)")
        return "\n".join(lines)
