"""CrewAI state adapter.

Converts between CrewAI task/crew state and framework-agnostic
ContextPackage state.
"""

from __future__ import annotations

from typing import Any

from handoff.models.context import AgentState


class CrewAIAdapter:
    """Adapter for CrewAI framework state.

    CrewAI organizes work around Crews, Agents, and Tasks.
    State is typically stored per-agent with ``role``, ``goal``, ``memory``
    and per-task with ``description``, ``output``, ``tools_used``.
    """

    @classmethod
    def from_crewai_state(
        cls,
        task_state: dict[str, Any],
        agent_state: dict[str, Any] | None = None,
    ) -> AgentState:
        """Convert CrewAI state to framework-agnostic AgentState.

        Args:
            task_state: CrewAI Task state dict.
            agent_state: Optional CrewAI Agent state dict.

        Returns:
            Normalized AgentState with CrewAI-specific schema marker.
        """
        variables: dict[str, Any] = {
            "crewai_task_description": task_state.get("description", ""),
            "crewai_task_output": task_state.get("output", ""),
            "crewai_task_expected_output": task_state.get("expected_output", ""),
            "crewai_task_tools": task_state.get("tools", []),
        }

        tool_history: list[dict[str, Any]] = []

        # Extract tool usage history if available
        if "tools_used" in task_state:
            for tool_call in task_state["tools_used"]:
                tool_history.append({
                    "tool": tool_call.get("name", "unknown"),
                    "arguments": tool_call.get("arguments", {}),
                    "result": tool_call.get("result", ""),
                })

        if agent_state:
            variables["crewai_agent_role"] = agent_state.get("role", "")
            variables["crewai_agent_goal"] = agent_state.get("goal", "")
            variables["crewai_agent_backstory"] = agent_state.get("backstory", "")
            if "memory" in agent_state:
                variables["crewai_agent_memory"] = agent_state["memory"]

        return AgentState(
            variables=variables,
            state_schema="crewai",
            tool_call_history=tool_history,
        )

    @classmethod
    def to_crewai_state(
        cls, agent_state: AgentState
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Convert AgentState back to CrewAI task + agent state.

        Args:
            agent_state: Framework-agnostic state.

        Returns:
            Tuple of (task_state, agent_state) dictionaries.
        """
        vars_ = agent_state.variables

        task_state: dict[str, Any] = {
            "description": vars_.get("crewai_task_description", ""),
            "output": vars_.get("crewai_task_output", ""),
            "expected_output": vars_.get("crewai_task_expected_output", ""),
            "tools": vars_.get("crewai_task_tools", []),
        }

        agent_state_out: dict[str, Any] = {
            "role": vars_.get("crewai_agent_role", ""),
            "goal": vars_.get("crewai_agent_goal", ""),
            "backstory": vars_.get("crewai_agent_backstory", ""),
        }

        if "crewai_agent_memory" in vars_:
            agent_state_out["memory"] = vars_["crewai_agent_memory"]

        # Restore tool history
        for tool_call in agent_state.tool_call_history:
            task_state.setdefault("tools_used", []).append({
                "name": tool_call.get("tool", ""),
                "arguments": tool_call.get("arguments", {}),
                "result": tool_call.get("result", ""),
            })

        return task_state, agent_state_out

    @classmethod
    def build_required_capabilities(
        cls,
        task_state: dict[str, Any],
    ) -> list[str]:
        """Extract capability requirements from a CrewAI task.

        Args:
            task_state: CrewAI Task state.

        Returns:
            List of capability tags (e.g., tool names).
        """
        caps: list[str] = ["crewai"]
        tools = task_state.get("tools", [])
        if tools:
            # Add tool class names or identifiers as capabilities
            for tool in tools:
                if isinstance(tool, str):
                    caps.append(tool)
                elif hasattr(tool, "name"):
                    caps.append(tool.name)
                elif hasattr(tool, "__class__"):
                    caps.append(tool.__class__.__name__)
        return caps
