"""Codex CLI adapter for handoff-relay.

Handles:
- Session file reading (via CodexSessionParser)
- AGENTS.md injection and cleanup
- Resume instruction generation
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from handoff.models.package import ContextPackage, PackageMeta, SourceInfo
from handoff.models.task import HandoffReason, ProgressSummary, TaskInfo

from handoff_relay.adapters.session_parser import CodexSessionParser
from handoff_relay.storage.local_store import LocalHandoffStore


HANDOFF_BLOCK_START = "<!-- handoff-inject-start -->"
HANDOFF_BLOCK_END = "<!-- handoff-inject-end -->"


class CodexAdapter:
    """Adapter for Codex CLI context capture and injection."""

    def __init__(
        self,
        store: LocalHandoffStore | None = None,
        session_dir: Path | str | None = None,
    ) -> None:
        self._store = store or LocalHandoffStore()
        self._parser = CodexSessionParser(session_dir)

    async def create_package(
        self,
        task_id: str,
        reason: HandoffReason,
        notes: str = "",
    ) -> dict[str, Any]:
        """Create a handoff package from the latest Codex session.

        Args:
            task_id: Task identifier.
            reason: Handoff reason.
            notes: Additional notes.

        Returns:
            Package metadata dict.
        """
        snapshot = self._parser.parse()

        # Codex sessions have messages, but when empty fall back to notes
        if snapshot.messages:
            current_step = snapshot.last_assistant_message or ""
            key_results = snapshot.last_user_message or ""
            blockers = notes
        else:
            current_step = notes or "Codex session"
            key_results = ""
            blockers = ""

        package = ContextPackage(
            meta=PackageMeta(
                source=SourceInfo(agent_id="codex-cli"),
                handoff_reason=reason,
            ),
            task=TaskInfo(
                original_task_id=task_id,
                description=snapshot.current_task or notes or "Codex session",
                progress_summary=ProgressSummary(
                    current_step=current_step,
                    key_intermediate_results=key_results,
                    blockers=blockers,
                ),
            ),
        )

        await self._store.save(package)

        return {
            "package_id": package.meta.package_id,
            "summary": package.task.progress_summary.to_markdown(),
            "file_path": str(self._store._package_path(package.meta.package_id)),
            "message_count": len(snapshot.messages),
        }

    def inject_into_agents_md(
        self,
        package_id: str,
        project_dir: Path | str,
        content: str | None = None,
    ) -> Path:
        """Inject handoff context into project's AGENTS.md.

        Codex CLI auto-discovers AGENTS.md hierarchically and injects it
        as an <INSTRUCTIONS> block in every session.

        Args:
            package_id: Package ID to inject.
            project_dir: Project root directory.
            content: Optional custom injectable content.

        Returns:
            Path to the AGENTS.md file.
        """
        project = Path(project_dir)
        agents_md = project / "AGENTS.md"

        # Create AGENTS.md if it doesn't exist
        if not agents_md.exists():
            agents_md.write_text(
                "# Agent Instructions\n\n", encoding="utf-8"
            )

        text = agents_md.read_text(encoding="utf-8")

        # Remove existing handoff block
        text = self._remove_handoff_block(text)

        # Generate injectable content
        if content is None:
            content = self._generate_injectable_markdown(package_id)

        inject_block = f"\n{HANDOFF_BLOCK_START}\n{content}\n{HANDOFF_BLOCK_END}\n"

        # Append to end of file
        new_text = text.rstrip() + "\n" + inject_block
        agents_md.write_text(new_text, encoding="utf-8")

        return agents_md

    def cleanup_agents_md(self, project_dir: Path | str) -> bool:
        """Remove handoff injection block from AGENTS.md.

        Args:
            project_dir: Project root directory.

        Returns:
            True if a block was removed.
        """
        project = Path(project_dir)
        agents_md = project / "AGENTS.md"

        if not agents_md.exists():
            return False

        text = agents_md.read_text(encoding="utf-8")
        new_text = self._remove_handoff_block(text)

        if new_text != text:
            agents_md.write_text(new_text, encoding="utf-8")
            return True
        return False

    def _remove_handoff_block(self, text: str) -> str:
        """Remove handoff injection block from text."""
        start_idx = text.find(HANDOFF_BLOCK_START)
        if start_idx == -1:
            return text

        end_idx = text.find(HANDOFF_BLOCK_END, start_idx)
        if end_idx == -1:
            return text

        end_idx += len(HANDOFF_BLOCK_END)
        return text[:start_idx].rstrip() + text[end_idx:].lstrip()

    def _generate_injectable_markdown(self, package_id: str) -> str:
        """Generate a markdown block for injection into AGENTS.md."""
        return f"""## Handoff Context

You are resuming work from a previous agent session.

- **Package ID**: `{package_id}`
- **Status**: Pending resume

When you begin working, review the progress summary and continue from the indicated next step.
"""

    def generate_resume_instructions(self, package_id: str) -> str:
        """Generate shell instructions for resuming work with this package."""
        return f"""# Resume from handoff

1. The handoff context has been injected into AGENTS.md.
2. Start Codex in this project directory:
   ```
   codex
   ```
3. Reference package ID if needed: `{package_id}`
"""
