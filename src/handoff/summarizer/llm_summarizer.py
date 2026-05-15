"""Progress summarization with LLM primary and rule-based fallback."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from handoff.models.context import ConversationState
from handoff.models.task import ProgressSummary

logger = logging.getLogger(__name__)


class Summarizer(ABC):
    """Abstract progress summarizer."""

    @abstractmethod
    async def summarize(
        self,
        task_description: str,
        conversation: ConversationState,
        state_variables: dict[str, Any],
    ) -> ProgressSummary:
        """Generate a progress summary for handoff.

        Args:
            task_description: The original task description.
            conversation: Current conversation state.
            state_variables: Current agent state variables.

        Returns:
            Structured progress summary.
        """
        raise NotImplementedError


class LLMSummarizer(Summarizer):
    """LLM-based progress summarizer.

    This is the primary summarizer per the spec. It delegates to an
    external LLM client. The actual LLM call is abstracted behind a
    callable so users can plug in OpenAI, Anthropic, or any provider.
    """

    SYSTEM_PROMPT = """You are a task progress summarizer for agent context handoff.
Your job is to analyze the conversation and state of an agent that is
handing off a task, and produce a structured summary so another agent
can seamlessly continue the work.

Produce a JSON object with these exact keys:
- completed_steps: list of strings describing what has been done
- current_step: string describing what the agent was in the middle of
- key_intermediate_results: string summarizing important findings or outputs
- blockers: string describing any problems or blockers encountered
- next_expected_action: string giving a clear instruction for what to do next

Be concise. Total output should be under 2000 characters."""

    def __init__(
        self,
        llm_client: Any | None = None,
        model: str = "gpt-4",
    ) -> None:
        """Initialize with an optional LLM client.

        Args:
            llm_client: Callable or client that accepts messages and returns
                a string response. Signature: ``client(messages, system) -> str``.
                If None, the summarizer will always fall back to rule-based.
            model: Model identifier to use.
        """
        self.llm_client = llm_client
        self.model = model

    async def summarize(
        self,
        task_description: str,
        conversation: ConversationState,
        state_variables: dict[str, Any],
    ) -> ProgressSummary:
        if self.llm_client is None:
            logger.warning("No LLM client configured; falling back to rule-based summarizer")
            fallback = RuleBasedFallbackSummarizer()
            summary = await fallback.summarize(task_description, conversation, state_variables)
            summary.generation_method = "rule_based_fallback"
            return summary

        # Build prompt content
        messages = self._build_messages(task_description, conversation, state_variables)

        try:
            raw_response = await self._call_llm(messages)
            summary = self._parse_response(raw_response)
            summary.generation_method = "llm"
            summary.raw_summary = raw_response
            return summary
        except Exception as exc:
            logger.error("LLM summarization failed: %s; falling back to rules", exc)
            fallback = RuleBasedFallbackSummarizer()
            fb_summary = await fallback.summarize(task_description, conversation, state_variables)
            fb_summary.generation_method = "rule_based_fallback"
            return fb_summary

    def _build_messages(
        self,
        task_description: str,
        conversation: ConversationState,
        state_variables: dict[str, Any],
    ) -> list[dict[str, str]]:
        """Construct the message payload for the LLM."""
        recent_msgs = conversation.messages[-20:]  # Last 20 messages max
        conv_text = "\n".join(
            f"{msg.role.value}: {msg.content[:500]}"
            for msg in recent_msgs
        )

        state_text = "\n".join(
            f"{k}: {str(v)[:200]}"
            for k, v in state_variables.items()
        )

        user_content = f"""Original task: {task_description}

Recent conversation:
{conv_text}

Current state variables:
{state_text}
"""
        return [{"role": "user", "content": user_content}]

    async def _call_llm(self, messages: list[dict[str, str]]) -> str:
        """Call the LLM client. Override or replace with your provider."""
        if self.llm_client is None:
            raise RuntimeError("LLM client is not configured")
        # Abstract interface - users provide their own client
        if hasattr(self.llm_client, "chat_completions"):
            # OpenAI-style
            response = await self.llm_client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": self.SYSTEM_PROMPT}] + messages,
                temperature=0.2,
                max_tokens=1000,
            )
            content: str = response.choices[0].message.content or ""
            return content
        elif callable(self.llm_client):
            result = await self.llm_client(
                messages=messages,
                system=self.SYSTEM_PROMPT,
            )
            if isinstance(result, str):
                return result
            return str(result)
        else:
            raise TypeError(f"Unsupported llm_client type: {type(self.llm_client)}")

    def _parse_response(self, raw: str) -> ProgressSummary:
        """Parse LLM JSON output into ProgressSummary."""
        import json
        import re

        # Extract JSON from markdown code blocks if present
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if json_match:
            raw = json_match.group(1)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Graceful degradation: extract what we can
            logger.warning("Failed to parse LLM response as JSON; using heuristic extraction")
            return self._heuristic_extract(raw)

        return ProgressSummary(
            completed_steps=data.get("completed_steps", []),
            current_step=data.get("current_step", ""),
            key_intermediate_results=data.get("key_intermediate_results", ""),
            blockers=data.get("blockers", ""),
            next_expected_action=data.get("next_expected_action", ""),
        )

    def _heuristic_extract(self, raw: str) -> ProgressSummary:
        """Extract fields heuristically when JSON parsing fails."""
        import re

        def extract(label: str) -> str:
            pattern = rf"{label}[:：]\s*(.+?)(?:\n\n|\n[A-Z]|$)"
            m = re.search(pattern, raw, re.IGNORECASE | re.DOTALL)
            return m.group(1).strip() if m else ""

        # Try to find bullet points for completed_steps
        steps_match = re.search(
            r"completed_steps.*?\[(.*?)\]",
            raw,
            re.IGNORECASE | re.DOTALL,
        )
        completed_steps: list[str] = []
        if steps_match:
            completed_steps = [
                s.strip().strip('"').strip("'")
                for s in steps_match.group(1).split(",")
            ]

        return ProgressSummary(
            completed_steps=completed_steps,
            current_step=extract("current_step"),
            key_intermediate_results=extract("key_intermediate_results"),
            blockers=extract("blockers"),
            next_expected_action=extract("next_expected_action"),
        )


class RuleBasedFallbackSummarizer(Summarizer):
    """Rule-based fallback summarizer when LLM is unavailable.

    Extracts structured information from conversation history without
    calling an external model. Useful for testing and resilience.
    """

    async def summarize(
        self,
        task_description: str,
        conversation: ConversationState,
        state_variables: dict[str, Any],
    ) -> ProgressSummary:
        messages = conversation.messages

        # Extract tool calls as "completed steps"
        completed_steps: list[str] = []
        for msg in messages:
            if msg.role.value == "tool":
                content = msg.content[:100]
                completed_steps.append(f"Tool result: {content}")

        # Last assistant message = current step
        current_step = ""
        for msg in reversed(messages):
            if msg.role.value == "assistant":
                current_step = msg.content[:200]
                break

        # State variables as intermediate results
        key_results = "; ".join(
            f"{k}={str(v)[:100]}"
            for k, v in list(state_variables.items())[:5]
        )

        # No explicit blocker detection in rules - leave empty
        blockers = ""

        # Next action: heuristic from last user message
        next_action = ""
        for msg in reversed(messages):
            if msg.role.value == "user":
                next_action = f"Continue working on: {msg.content[:200]}"
                break

        return ProgressSummary(
            completed_steps=completed_steps[-10:] if completed_steps else ["Task initiated"],
            current_step=current_step or "Task in progress",
            key_intermediate_results=key_results,
            blockers=blockers,
            next_expected_action=next_action or "Continue task execution",
            generation_method="rule_based",
        )
