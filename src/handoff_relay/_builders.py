"""Pure helpers for building and formatting handoff packages.

Extracted from mcp_server.py to avoid circular imports between the MCP server
and the HandoffRelayService layer.
"""

from __future__ import annotations

from typing import Any

from handoff.models.context import (
    AgentState,
    ConversationMessage,
    ConversationState,
    MessageRole,
)
from handoff.models.package import ContextBody, ContextPackage, PackageMeta, SourceInfo
from handoff.models.task import HandoffReason, ProgressSummary, TaskInfo


def build_capture_package(
    capture_id: str,
    agent_type: str,
    messages: list[dict[str, Any]],
    variables: dict[str, Any],
    current_step: str,
    blockers: list[str],
) -> ContextPackage:
    """Build a ContextPackage from captured agent session state.

    This is a pure helper extracted for testability — tests can call it
    directly without spinning up the full MCP server.
    """

    def _map_role(role: str) -> MessageRole:
        try:
            return MessageRole(role)
        except ValueError:
            return MessageRole.ASSISTANT if role in ("assistant", "model", "agent") else MessageRole.USER

    conv_messages = [
        ConversationMessage(
            role=_map_role(m.get("role", "user")),
            content=str(m.get("content", "")),
            metadata={k: v for k, v in m.items() if k not in ("role", "content")},
        )
        for m in messages
    ]

    return ContextPackage(
        meta=PackageMeta(
            package_id=capture_id,
            source=SourceInfo(agent_id=agent_type),
            handoff_reason=HandoffReason.USER_TRIGGERED,
        ),
        task=TaskInfo(
            original_task_id=capture_id,
            description=current_step or f"{agent_type} captured state",
            progress_summary=ProgressSummary(
                current_step=current_step,
                key_intermediate_results=f"Captured {len(variables)} state variable(s)",
                blockers="; ".join(blockers) if blockers else "",
            ),
        ),
        context=ContextBody(
            conversation=ConversationState(messages=conv_messages),
            state=AgentState(variables=variables),
        ),
    )


def format_injectable(package: ContextPackage) -> str:
    """Format package as injectable markdown block."""
    ps = package.task.progress_summary
    return f"""## Handoff Context

**Source**: {package.meta.source.agent_id}
**Task**: {package.task.description}
**Package ID**: `{package.meta.package_id}`

### Progress Summary
- **Completed Steps**: {', '.join(ps.completed_steps) if ps.completed_steps else 'N/A'}
- **Current Step**: {ps.current_step or 'N/A'}
- **Key Results**: {ps.key_intermediate_results or 'N/A'}
- **Blockers**: {ps.blockers or 'N/A'}
- **Next Step**: {ps.next_expected_action or 'N/A'}
"""


def format_system_prompt_addition(package: ContextPackage) -> str:
    """Format a concise system prompt addition."""
    ps = package.task.progress_summary
    return (
        f"You are resuming work from a previous {package.meta.source.agent_id} session. "
        f"Task: {package.task.description}. "
        f"Current step: {ps.current_step or 'N/A'}. "
        f"Next expected action: {ps.next_expected_action or 'Continue task execution'}."
    )
