"""Tests for cross-framework state adapters."""

from __future__ import annotations


from handoff.framework_adapter.crewai_adapter import CrewAIAdapter
from handoff.framework_adapter.langgraph_adapter import LangGraphAdapter
from handoff.models.context import AgentState


class TestLangGraphAdapter:
    """LangGraph state conversion tests."""

    def test_from_langgraph_state(self) -> None:
        lang_state = {
            "messages": [
                {"type": "human", "content": "Hello"},
                {"type": "ai", "content": "Hi"},
            ],
            "my_var": 42,
            "__last_node__": "some_node",
            "__run_id__": "run-1",
        }
        agent_state = LangGraphAdapter.from_langgraph_state(lang_state)
        assert agent_state.state_schema == "langgraph"
        assert "my_var" in agent_state.variables
        assert agent_state.variables["my_var"] == 42
        assert "messages" in agent_state.variables
        # Internal keys stripped
        assert "__last_node__" not in agent_state.variables

    def test_to_langgraph_state(self) -> None:
        agent_state = AgentState(
            variables={"my_var": 42, "messages": [{"type": "human", "content": "Hi"}]},
            state_schema="langgraph",
        )
        lang_state = LangGraphAdapter.to_langgraph_state(agent_state)
        assert lang_state["my_var"] == 42
        assert "messages" in lang_state

    def test_serialize_langchain_messages(self) -> None:
        class FakeMessage:
            def model_dump(self):
                return {"type": "human", "content": "test"}

        result = LangGraphAdapter._serialize_messages([FakeMessage()])
        assert result == [{"type": "human", "content": "test"}]

    def test_serialize_plain_dict_messages(self) -> None:
        result = LangGraphAdapter._serialize_messages([{"type": "ai", "content": "ok"}])
        assert result == [{"type": "ai", "content": "ok"}]


class TestCrewAIAdapter:
    """CrewAI state conversion tests."""

    def test_from_crewai_state(self) -> None:
        task_state = {
            "description": "Research topic",
            "output": "Partial result",
            "expected_output": "Full report",
            "tools": ["search", "summarize"],
            "tools_used": [
                {"name": "search", "arguments": {"q": "AI"}, "result": "found"},
            ],
        }
        agent_state_dict = {
            "role": "Researcher",
            "goal": "Find information",
            "backstory": "Expert in AI",
        }
        result = CrewAIAdapter.from_crewai_state(task_state, agent_state_dict)
        assert result.state_schema == "crewai"
        assert result.variables["crewai_task_description"] == "Research topic"
        assert result.variables["crewai_agent_role"] == "Researcher"
        assert len(result.tool_call_history) == 1
        assert result.tool_call_history[0]["tool"] == "search"

    def test_from_crewai_state_no_agent(self) -> None:
        task_state = {"description": "Simple task", "output": ""}
        result = CrewAIAdapter.from_crewai_state(task_state)
        assert result.variables["crewai_task_description"] == "Simple task"
        assert "crewai_agent_role" not in result.variables

    def test_to_crewai_state(self) -> None:
        agent_state = AgentState(
            variables={
                "crewai_task_description": "Do X",
                "crewai_task_output": "Done",
                "crewai_agent_role": "Worker",
            },
            state_schema="crewai",
            tool_call_history=[
                {"tool": "search", "arguments": {}, "result": "ok"},
            ],
        )
        task_state, agent_state_out = CrewAIAdapter.to_crewai_state(agent_state)
        assert task_state["description"] == "Do X"
        assert task_state["output"] == "Done"
        assert agent_state_out["role"] == "Worker"
        assert len(task_state.get("tools_used", [])) == 1

    def test_build_required_capabilities(self) -> None:
        task_state = {"tools": ["search", "scrape"]}
        caps = CrewAIAdapter.build_required_capabilities(task_state)
        assert "crewai" in caps
        assert "search" in caps
        assert "scrape" in caps
