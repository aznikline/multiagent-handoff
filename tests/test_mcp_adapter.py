"""Tests for MCP Server adapter."""

from __future__ import annotations

import json

import pytest

from handoff.mcp_adapter.server import create_mcp_server
from handoff.orchestrator.orchestrator import HandoffOrchestrator


class TestMCPServer:
    """MCP server creation and tool tests."""

    @pytest.fixture
    def orchestrator(self) -> HandoffOrchestrator:
        return HandoffOrchestrator()

    @pytest.fixture
    def server(self, orchestrator: HandoffOrchestrator):
        pytest.importorskip("mcp")
        return create_mcp_server(orchestrator, name="test-handoff")

    def test_server_creation(self, server) -> None:
        assert server is not None
        assert server.name == "test-handoff"

    @pytest.mark.asyncio
    async def test_tools_registered(self, server) -> None:
        tools = await server.list_tools()
        tool_names = {t.name for t in tools}
        assert "pack_handoff_context" in tool_names
        assert "get_handoff_status" in tool_names
        assert "cleanup_expired_handoffs" in tool_names
        assert "list_audit_log" in tool_names

    @pytest.mark.asyncio
    async def test_resource_templates_registered(self, server) -> None:
        templates = await server.list_resource_templates()
        uris = [str(t.uriTemplate) for t in templates]
        assert any("handoff://" in uri for uri in uris)

    @pytest.mark.asyncio
    async def test_prompts_registered(self, server) -> None:
        prompts = await server.list_prompts()
        prompt_names = {p.name for p in prompts}
        assert "handoff_resume_prompt" in prompt_names

    @pytest.mark.asyncio
    async def test_pack_handoff_context_tool(self, server) -> None:
        result = await server.call_tool(
            "pack_handoff_context",
            {
                "task_id": "task-1",
                "source_agent_id": "agent-a",
                "reason": "user_triggered",
                "task_description": "Test task",
                "progress_summary": "In progress",
            },
        )
        # call_tool returns (Sequence[ContentBlock], metadata dict)
        content_blocks, _meta = result
        assert len(content_blocks) >= 1
        data = content_blocks[0].text
        parsed = json.loads(data)
        assert parsed["status"] in ("pending", "stored")
        assert "package_id" in parsed

    @pytest.mark.asyncio
    async def test_get_handoff_status_tool(self, server) -> None:
        # First create a package
        result = await server.call_tool(
            "pack_handoff_context",
            {
                "task_id": "task-2",
                "source_agent_id": "agent-b",
                "reason": "token_limit",
                "task_description": "Another task",
                "progress_summary": "Done",
            },
        )
        content_blocks, _meta = result
        data = json.loads(content_blocks[0].text)
        package_id = data["package_id"]

        status_result = await server.call_tool(
            "get_handoff_status",
            {"package_id": package_id},
        )
        status_blocks, _meta = status_result
        status_data = json.loads(status_blocks[0].text)
        assert status_data["status"] == "stored"
