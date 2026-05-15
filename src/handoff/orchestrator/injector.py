"""Context injection strategies for target agents."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from handoff.models.package import ContextPackage
from handoff.models.task import TaskStatus


class InjectionError(Exception):
    """Raised when context injection fails."""

    pass


class ContextInjector(ABC):
    """Abstract strategy for injecting handoff context into a target agent."""

    @abstractmethod
    async def inject(
        self,
        target_agent_id: str,
        package: ContextPackage,
        original_system_prompt: str | None = None,
    ) -> dict[str, Any]:
        """Inject context into the target agent and return session metadata.

        Args:
            target_agent_id: Identifier of the receiving agent.
            package: The context package to inject.
            original_system_prompt: The target agent's existing system prompt
                (if any), to be preserved and extended.

        Returns:
            Session metadata including session_id and any warnings.

        Raises:
            InjectionError: If injection fails.
        """
        raise NotImplementedError


class PromptBasedInjector(ContextInjector):
    """Injects context via system prompt modification.

    This is the most framework-agnostic approach. It constructs a new
    system prompt containing the progress summary and conversation history,
    then returns it for the orchestrator to apply through the target
    agent's runtime API.
    """

    MAX_HISTORY_MESSAGES: int = 20
    MAX_SUMMARY_LENGTH: int = 2000

    async def inject(
        self,
        target_agent_id: str,
        package: ContextPackage,
        original_system_prompt: str | None = None,
    ) -> dict[str, Any]:
        if package.is_expired():
            raise InjectionError(f"Package {package.meta.package_id} has expired")

        # Build handoff system prompt section
        handoff_section = self._build_handoff_prompt(package)

        # Assemble full system prompt
        parts: list[str] = []
        if original_system_prompt:
            parts.append(original_system_prompt)
        parts.append(handoff_section)
        full_prompt = "\n\n".join(parts)

        # Build message history for injection (recent messages only)
        messages = self._prepare_message_history(package)

        # Warn if token budget may be exceeded
        warnings: list[str] = []
        if len(messages) >= self.MAX_HISTORY_MESSAGES:
            warnings.append(
                f"Message history truncated to {self.MAX_HISTORY_MESSAGES} messages"
            )

        return {
            "target_agent_id": target_agent_id,
            "session_id": f"handoff-{package.meta.package_id}",
            "system_prompt": full_prompt,
            "messages": messages,
            "state_variables": package.context.state.variables,
            "required_capabilities": package.task.required_capabilities,
            "task_status": TaskStatus.IN_PROGRESS,
            "warnings": warnings,
        }

    def _build_handoff_prompt(self, package: ContextPackage) -> str:
        """Construct the handoff instruction block."""
        summary = package.task.progress_summary
        meta = package.meta

        lines = [
            "## 交接任务说明",
            "",
            f"你正在接续一个由 **{meta.source.agent_id}**（角色：{meta.source.agent_role}）处理的任务。",
            f"交接原因：**{meta.handoff_reason.value}**",
            f"优先级：**{meta.priority.value}**",
            "",
            "### 原始任务",
            package.task.description,
            "",
        ]

        # Add progress summary
        if summary.current_step or summary.completed_steps:
            lines.append(summary.to_markdown())
            lines.append("")

        # Add truncation notice if applicable
        if meta.truncation.applied and meta.truncation.summary_prefix:
            lines.append("### 早期对话摘要")
            lines.append(meta.truncation.summary_prefix)
            lines.append("")

        # Add expected output format if available
        if package.task.expected_output_format:
            lines.append("### 期望输出格式")
            lines.append(package.task.expected_output_format)
            lines.append("")

        lines.append("---")
        lines.append("请基于以上信息继续任务。如有不确定之处，请明确说明。")

        return "\n".join(lines)

    def _prepare_message_history(
        self, package: ContextPackage
    ) -> list[dict[str, Any]]:
        """Extract recent messages for injection, respecting limits."""
        all_messages = package.context.conversation.messages
        # Take the most recent messages up to the limit
        recent = all_messages[-self.MAX_HISTORY_MESSAGES :]

        return [
            {
                "role": msg.role.value,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
            }
            for msg in recent
        ]
