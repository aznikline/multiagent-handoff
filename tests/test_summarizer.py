"""Tests for progress summarizers."""

from __future__ import annotations

import pytest

from handoff.models.context import (
    ConversationMessage,
    ConversationState,
    MessageRole,
)
from handoff.summarizer.llm_summarizer import (
    LLMSummarizer,
    RuleBasedFallbackSummarizer,
)


class TestRuleBasedFallbackSummarizer:
    """Rule-based summarizer tests."""

    @pytest.fixture
    def summarizer(self) -> RuleBasedFallbackSummarizer:
        return RuleBasedFallbackSummarizer()

    @pytest.fixture
    def conversation(self) -> ConversationState:
        return ConversationState(
            messages=[
                ConversationMessage(role=MessageRole.USER, content="Search for papers"),
                ConversationMessage(role=MessageRole.ASSISTANT, content="I found 3 papers"),
                ConversationMessage(role=MessageRole.TOOL, content='{"results": [{"title": "Q1"}]}'),
                ConversationMessage(role=MessageRole.USER, content="Summarize them"),
            ],
        )

    @pytest.mark.asyncio
    async def test_extracts_tool_calls_as_steps(
        self,
        summarizer: RuleBasedFallbackSummarizer,
        conversation: ConversationState,
    ) -> None:
        result = await summarizer.summarize(
            task_description="Research quantum",
            conversation=conversation,
            state_variables={"count": 3},
        )
        assert len(result.completed_steps) > 0
        assert any("Tool result" in step for step in result.completed_steps)

    @pytest.mark.asyncio
    async def test_current_step_from_last_assistant(
        self,
        summarizer: RuleBasedFallbackSummarizer,
        conversation: ConversationState,
    ) -> None:
        result = await summarizer.summarize(
            task_description="Research quantum",
            conversation=conversation,
            state_variables={},
        )
        assert "found 3 papers" in result.current_step

    @pytest.mark.asyncio
    async def test_next_action_from_last_user(
        self,
        summarizer: RuleBasedFallbackSummarizer,
        conversation: ConversationState,
    ) -> None:
        result = await summarizer.summarize(
            task_description="Research quantum",
            conversation=conversation,
            state_variables={},
        )
        assert "Summarize them" in result.next_expected_action

    @pytest.mark.asyncio
    async def test_includes_state_variables(
        self,
        summarizer: RuleBasedFallbackSummarizer,
        conversation: ConversationState,
    ) -> None:
        result = await summarizer.summarize(
            task_description="Research",
            conversation=conversation,
            state_variables={"topic": "quantum", "count": 5},
        )
        assert "quantum" in result.key_intermediate_results
        assert "5" in result.key_intermediate_results

    @pytest.mark.asyncio
    async def test_generation_method(self) -> None:
        summarizer = RuleBasedFallbackSummarizer()
        result = await summarizer.summarize(
            task_description="Test",
            conversation=ConversationState(),
            state_variables={},
        )
        assert result.generation_method == "rule_based"

    @pytest.mark.asyncio
    async def test_empty_conversation(self) -> None:
        summarizer = RuleBasedFallbackSummarizer()
        result = await summarizer.summarize(
            task_description="Test",
            conversation=ConversationState(),
            state_variables={},
        )
        assert result.current_step == "Task in progress"
        assert result.next_expected_action == "Continue task execution"


class TestLLMSummarizer:
    """LLM summarizer tests with mock client."""

    @pytest.fixture
    def mock_llm_client(self):
        """Returns a mock async LLM client."""

        class MockClient:
            async def chat_completions_create(self, **kwargs):
                class Choice:
                    class Message:
                        content = '{"completed_steps": ["Searched"], "current_step": "Reading", "key_intermediate_results": "Found 3", "blockers": "None", "next_expected_action": "Summarize"}'
                    message = Message()
                class Response:
                    choices = [Choice()]
                return Response()

        return MockClient()

    @pytest.mark.asyncio
    async def test_fallback_when_no_client(self) -> None:
        summarizer = LLMSummarizer(llm_client=None)
        conversation = ConversationState(
            messages=[
                ConversationMessage(role=MessageRole.USER, content="Hello"),
            ],
        )
        result = await summarizer.summarize(
            task_description="Test",
            conversation=conversation,
            state_variables={},
        )
        assert result.generation_method == "rule_based_fallback"

    @pytest.mark.asyncio
    async def test_parse_json_response(self) -> None:
        mock_client = type("Mock", (), {})()

        async def mock_call(*, messages, system):
            return '{"completed_steps": ["Step 1"], "current_step": "Step 2", "key_intermediate_results": "Result", "blockers": "", "next_expected_action": "Next"}'

        mock_client.chat = type("Chat", (), {"completions": type("Completions", (), {"create": mock_call})()})()
        # Use callable form
        summarizer = LLMSummarizer(llm_client=mock_call)
        conversation = ConversationState(
            messages=[
                ConversationMessage(role=MessageRole.USER, content="Do research"),
            ],
        )
        result = await summarizer.summarize(
            task_description="Research",
            conversation=conversation,
            state_variables={},
        )
        assert result.completed_steps == ["Step 1"]
        assert result.current_step == "Step 2"
        assert result.generation_method == "llm"

    @pytest.mark.asyncio
    async def test_heuristic_extraction_on_bad_json(self) -> None:
        async def mock_call(*, messages, system):
            return "completed_steps: [Step A]\ncurrent_step: Step B\nkey_intermediate_results: Result\nblockers: None\nnext_expected_action: Do C"

        summarizer = LLMSummarizer(llm_client=mock_call)
        conversation = ConversationState(
            messages=[
                ConversationMessage(role=MessageRole.USER, content="Test"),
            ],
        )
        result = await summarizer.summarize(
            task_description="Test",
            conversation=conversation,
            state_variables={},
        )
        assert result.current_step == "Step B"
        assert "Step A" in result.completed_steps

    @pytest.mark.asyncio
    async def test_fallback_on_llm_error(self) -> None:
        async def failing_call(*, messages, system):
            raise RuntimeError("LLM service down")

        summarizer = LLMSummarizer(llm_client=failing_call)
        conversation = ConversationState(
            messages=[
                ConversationMessage(role=MessageRole.USER, content="Test"),
            ],
        )
        result = await summarizer.summarize(
            task_description="Test",
            conversation=conversation,
            state_variables={},
        )
        assert result.generation_method == "rule_based_fallback"

    def test_build_messages_truncates(self) -> None:
        summarizer = LLMSummarizer()
        messages = [
            ConversationMessage(role=MessageRole.USER, content=f"Message {i}")
            for i in range(25)
        ]
        conversation = ConversationState(messages=messages)
        built = summarizer._build_messages("Task", conversation, {})
        assert len(built) == 1
        # Should only include last 20 messages
        assert "Message 24" in built[0]["content"]
