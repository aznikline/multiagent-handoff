"""MCP Server exposing handoff capabilities via FastMCP.

Integrates with the official MCP Python SDK to provide:
- Tools: pack_context, get_status, cleanup_expired
- Resources: handoff://{package_id}
- Prompts: handoff_system_prompt

Usage:
    server = create_mcp_server(orchestrator)
    server.run(transport="stdio")
"""

from __future__ import annotations

from typing import Any

from handoff.models.package import ContextPackage
from handoff.models.task import HandoffReason
from handoff.orchestrator.orchestrator import HandoffOrchestrator


def create_mcp_server(
    orchestrator: HandoffOrchestrator,
    name: str = "handoff-orchestrator",
) -> Any:
    """Create and configure a FastMCP server for handoff operations.

    Args:
        orchestrator: The handoff orchestrator instance to expose.
        name: Server name identifier.

    Returns:
        Configured FastMCP server instance.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise ImportError(
            "MCP integration requires 'mcp'. Install with: "
            "pip install agent-context-handoff[mcp]"
        ) from exc

    mcp = FastMCP(name)

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    @mcp.tool()
    async def pack_handoff_context(
        task_id: str,
        source_agent_id: str,
        reason: str,
        task_description: str,
        progress_summary: str,
    ) -> dict[str, Any]:
        """Package and store a context handoff for later retrieval.

        Args:
            task_id: Unique identifier for the task.
            source_agent_id: ID of the agent initiating the handoff.
            reason: Handoff reason (token_limit, task_delegation, error_recovery, user_triggered).
            task_description: Description of the original task.
            progress_summary: Human-readable summary of current progress.

        Returns:
            Handoff result with package_id and status.
        """
        from handoff.models.package import PackageMeta, SourceInfo
        from handoff.models.task import TaskInfo, ProgressSummary

        package = ContextPackage(
            meta=PackageMeta(
                source=SourceInfo(agent_id=source_agent_id),
                handoff_reason=HandoffReason(reason),
            ),
            task=TaskInfo(
                original_task_id=task_id,
                description=task_description,
                progress_summary=ProgressSummary(
                    current_step=progress_summary,
                ),
            ),
        )

        result = await orchestrator.initiate(
            source_agent_id=source_agent_id,
            reason=HandoffReason(reason),
            package=package,
        )
        return result.to_dict()

    @mcp.tool()
    async def get_handoff_status(package_id: str) -> dict[str, Any]:
        """Get the current status of a handoff package.

        Args:
            package_id: The package ID to query.

        Returns:
            Status dictionary or error if not found.
        """
        return await orchestrator.get_status(package_id)

    @mcp.tool()
    async def cleanup_expired_handoffs() -> dict[str, Any]:
        """Remove all expired handoff packages from storage.

        Returns:
            Dictionary with count of cleaned packages.
        """
        count = await orchestrator.cleanup_expired()
        return {"cleaned_count": count}

    @mcp.tool()
    async def list_audit_log(limit: int = 100) -> list[dict[str, Any]]:
        """Return recent audit log entries.

        Args:
            limit: Maximum number of entries to return.

        Returns:
            List of audit log entries.
        """
        log = orchestrator.get_audit_log()
        return log[-limit:]

    # ------------------------------------------------------------------
    # Resources
    # ------------------------------------------------------------------

    @mcp.resource("handoff://{package_id}")
    async def get_handoff_package(package_id: str) -> str:
        """Retrieve a serialized context package by ID.

        Args:
            package_id: The handoff package identifier.

        Returns:
            JSON string of the ContextPackage.
        """
        from handoff.serialization.serializer import JsonSerializer

        package = await orchestrator.store.load(package_id)
        if package is None:
            return "{}"
        serializer = JsonSerializer()
        return serializer.serialize(package).decode("utf-8")

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    @mcp.prompt()
    async def handoff_resume_prompt(package_id: str) -> str:
        """Generate a system prompt for resuming a handed-off task.

        Args:
            package_id: The handoff package to resume from.

        Returns:
            Structured system prompt with progress summary.
        """
        package = await orchestrator.store.load(package_id)
        if package is None:
            return "Error: Package not found"

        from handoff.orchestrator.injector import PromptBasedInjector

        injector = PromptBasedInjector()
        result = await injector.inject("target", package)
        prompt: str = result.get("system_prompt", "")
        return prompt

    return mcp
