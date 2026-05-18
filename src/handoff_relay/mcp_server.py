"""MCP Server for handoff-relay.

Exposes tools for local CLI agents to create, retrieve, and manage
handoff packages via the Model Context Protocol.
"""

from __future__ import annotations

from typing import Any

from handoff_relay.service import HandoffRelayService
from handoff_relay.storage.local_store import LocalHandoffStore


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
    service = HandoffRelayService(store=LocalHandoffStore())

    @mcp.tool()
    async def handoff_create_package(
        source_agent: str,
        task_id: str,
        reason: str = "user_triggered",
        target_agent_type: str = "any",
        include_full_history: bool = False,
        notes: str = "",
    ) -> dict[str, Any]:
        """Create a context handoff package from the current session state."""
        return await service.create_package(
            source_agent=source_agent,
            task_id=task_id,
            reason=reason,
            notes=notes,
        )

    @mcp.tool()
    async def handoff_get_package(
        package_id: str,
        format: str = "summary",
    ) -> dict[str, Any]:
        """Retrieve a previously created handoff package."""
        return await service.get_package(package_id=package_id, format=format)

    @mcp.tool()
    async def handoff_list_packages(
        status: str | None = None,
        source_agent: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List all available handoff packages."""
        return await service.list_packages(
            status=status,
            source_agent=source_agent,
            limit=limit,
        )

    @mcp.tool()
    async def handoff_capture_state(
        agent_type: str,
        messages: list[dict[str, Any]],
        variables: dict[str, Any],
        current_step: str,
        blockers: list[str],
    ) -> dict[str, Any]:
        """Capture the current agent session state for later handoff."""
        return await service.capture_state(
            agent_type=agent_type,
            messages=messages,
            variables=variables,
            current_step=current_step,
            blockers=blockers,
        )

    @mcp.tool()
    async def handoff_get_injectable_context(
        package_id: str,
        target_agent: str,
        max_tokens: int = 2000,
    ) -> dict[str, Any]:
        """Get handoff context formatted for injection into target agent."""
        return await service.get_injectable_context(
            package_id=package_id,
            target_agent=target_agent,
            max_tokens=max_tokens,
        )

    await mcp.run_stdio_async()
