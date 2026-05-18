"""MCP Server for handoff-relay.

Exposes tools for local CLI agents to create, retrieve, and manage
handoff packages via the Model Context Protocol.
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

from handoff_relay.adapters.claude_code import ClaudeCodeAdapter
from handoff_relay.adapters.session_parser import get_parser
from handoff_relay.storage.local_store import LocalHandoffStore
from handoff_relay._utils import normalize_reason


async def serve_mcp() -> None:
    """Start the MCP server with handoff-relay tools."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise ImportError(
            "MCP server requires 'mcp'. Install with: "
            "pip install agent-context-handoff[mcp]"
        ) from exc

    mcp = FastMCP("handoff-relay")
    store = LocalHandoffStore()

    @mcp.tool()
    async def handoff_create_package(
        source_agent: str,
        task_id: str,
        reason: str = "user_triggered",
        target_agent_type: str = "any",
        include_full_history: bool = False,
        notes: str = "",
    ) -> dict[str, Any]:
        """Create a context handoff package from the current session state.

        Args:
            source_agent: Source agent type (claude-code, opencode, codex-cli).
            task_id: Task identifier.
            reason: Handoff reason (token_limit, rate_limit, user_triggered, error_recovery).
            target_agent_type: Preferred target agent for format optimization.
            include_full_history: Include full conversation history.
            notes: Additional handoff notes.

        Returns:
            Package metadata with package_id, summary, and file_path.
        """
        if source_agent == "claude-code":
            adapter = ClaudeCodeAdapter(store=store)
            result = await adapter.create_package(
                task_id=task_id,
                reason=HandoffReason(normalize_reason(reason)),
                notes=notes,
            )
            return {
                "package_id": result["package_id"],
                "summary": result["summary"],
                "file_path": result["file_path"],
            }

        # Generic path
        parser = get_parser(source_agent)
        snapshot = parser.parse()

        package = ContextPackage(
            meta=PackageMeta(
                source=SourceInfo(agent_id=source_agent),
                handoff_reason=HandoffReason(normalize_reason(reason)),
            ),
            task=TaskInfo(
                original_task_id=task_id,
                description=snapshot.current_task or f"{source_agent} session",
                progress_summary=ProgressSummary(
                    current_step=snapshot.last_assistant_message or "",
                    key_intermediate_results=snapshot.last_user_message or "",
                    blockers=notes,
                ),
            ),
        )

        await store.save(package)

        return {
            "package_id": package.meta.package_id,
            "summary": package.task.progress_summary.to_markdown(),
            "file_path": str(store._package_path(package.meta.package_id)),
        }

    @mcp.tool()
    async def handoff_get_package(
        package_id: str,
        format: str = "summary",
    ) -> dict[str, Any]:
        """Retrieve a previously created handoff package.

        Args:
            package_id: Package ID to retrieve.
            format: Output format (full, summary, injectable).

        Returns:
            Package data in requested format.
        """
        package = await store.load(package_id)
        if package is None:
            return {"error": f"Package not found: {package_id}"}

        if format == "full":
            return {"package": package.model_dump()}
        elif format == "injectable":
            return {
                "injectable_markdown": _format_injectable(package),
                "system_prompt_addition": _format_system_prompt_addition(package),
            }
        else:
            return {
                "summary": package.task.progress_summary.to_markdown(),
                "package_id": package_id,
                "status": "stored",
            }

    @mcp.tool()
    async def handoff_list_packages(
        status: str | None = None,
        source_agent: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List all available handoff packages.

        Args:
            status: Filter by status (pending, in_progress, completed, archived).
            source_agent: Filter by source agent type.
            limit: Maximum number of results.

        Returns:
            List of package summaries.
        """
        packages = await store.list_packages(
            status=status,
            source_agent=source_agent,
            limit=limit,
        )
        return {"packages": packages, "count": len(packages)}

    @mcp.tool()
    async def handoff_capture_state(
        agent_type: str,
        messages: list[dict[str, Any]],
        variables: dict[str, Any],
        current_step: str,
        blockers: list[str],
    ) -> dict[str, Any]:
        """Capture the current agent session state for later handoff.

        The captured state is persisted to local storage and can be
        retrieved via handoff_get_package using the returned capture_id.

        Args:
            agent_type: Agent type (opencode, claude-code, codex-cli).
            messages: Recent conversation messages.
            variables: Current agent state variables.
            current_step: Description of current task step.
            blockers: Current blockers or issues.

        Returns:
            Capture metadata with capture_id, estimated token count, and status.
        """
        import json
        import uuid

        capture_id = f"capture-{uuid.uuid4().hex[:8]}"
        package = _build_capture_package(
            capture_id=capture_id,
            agent_type=agent_type,
            messages=messages,
            variables=variables,
            current_step=current_step,
            blockers=blockers,
        )
        await store.save(package)

        payload_size = len(json.dumps(messages)) + len(json.dumps(variables))
        estimated_tokens = payload_size // 4

        return {
            "capture_id": capture_id,
            "estimated_token_count": estimated_tokens,
            "status": "captured",
            "package_id": capture_id,
        }

    @mcp.tool()
    async def handoff_get_injectable_context(
        package_id: str,
        target_agent: str,
        max_tokens: int = 2000,
    ) -> dict[str, Any]:
        """Get handoff context formatted for injection into target agent.

        Args:
            package_id: Package ID to inject.
            target_agent: Target agent type (opencode, claude-code, codex-cli).
            max_tokens: Maximum token budget for injected context.

        Returns:
            Injectable markdown and system prompt addition.
        """
        package = await store.load(package_id)
        if package is None:
            return {"error": f"Package not found: {package_id}"}

        injectable = _format_injectable(package)
        # Rough truncation to token budget
        if len(injectable) > max_tokens * 4:
            injectable = injectable[: max_tokens * 4] + "\n\n...[truncated]"

        return {
            "injectable_markdown": injectable,
            "system_prompt_addition": _format_system_prompt_addition(package),
            "target_agent": target_agent,
            "package_id": package_id,
        }

    await mcp.run_stdio_async()


def _build_capture_package(
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


def _format_injectable(package: ContextPackage) -> str:
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


def _format_system_prompt_addition(package: ContextPackage) -> str:
    """Format a concise system prompt addition."""
    ps = package.task.progress_summary
    return (
        f"You are resuming work from a previous {package.meta.source.agent_id} session. "
        f"Task: {package.task.description}. "
        f"Current step: {ps.current_step or 'N/A'}. "
        f"Next expected action: {ps.next_expected_action or 'Continue task execution'}."
    )
