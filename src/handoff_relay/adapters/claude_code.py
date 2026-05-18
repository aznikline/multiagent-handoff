"""Claude Code adapter for handoff-relay.

Handles:
- Session file reading
- CLAUDE.md injection and cleanup
- Hook script generation
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from handoff.models.package import ContextPackage, PackageMeta, SourceInfo
from handoff.models.task import HandoffReason, ProgressSummary, TaskInfo

from handoff_relay.adapters.session_parser import ClaudeCodeSessionParser
from handoff_relay.storage.local_store import LocalHandoffStore


HANDOFF_BLOCK_START = "<!-- handoff-inject-start -->"
HANDOFF_BLOCK_END = "<!-- handoff-inject-end -->"

DEFAULT_CLAUDE_DIR = Path.home() / ".claude"
DEFAULT_CLAUDE_MD = Path("CLAUDE.md")


class ClaudeCodeAdapter:
    """Adapter for Claude Code context capture and injection."""

    def __init__(
        self,
        store: LocalHandoffStore | None = None,
        session_dir: Path | str | None = None,
    ) -> None:
        self._store = store or LocalHandoffStore()
        self._parser = ClaudeCodeSessionParser(session_dir)

    async def create_package(
        self,
        task_id: str,
        reason: HandoffReason,
        notes: str = "",
    ) -> dict[str, Any]:
        """Create a handoff package from the latest Claude Code session.

        Args:
            task_id: Task identifier.
            reason: Handoff reason.
            notes: Additional notes.

        Returns:
            Package metadata dict.
        """
        snapshot = self._parser.parse()

        package = ContextPackage(
            meta=PackageMeta(
                source=SourceInfo(agent_id="claude-code"),
                handoff_reason=reason,
            ),
            task=TaskInfo(
                original_task_id=task_id,
                description=snapshot.current_task or "Claude Code session",
                progress_summary=ProgressSummary(
                    current_step=snapshot.last_assistant_message or "",
                    key_intermediate_results=snapshot.last_user_message or "",
                    blockers=notes,
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

    def _resolve_claude_md(self, project: Path) -> Path:
        """Resolve the real CLAUDE.md path, avoiding symlink to AGENTS.md.

        If CLAUDE.md is a symlink to AGENTS.md, returns a separate
        `.claude/CLAUDE.md` path to avoid polluting the shared file.
        """
        claude_md = project / "CLAUDE.md"
        if claude_md.exists() and claude_md.is_symlink():
            target = claude_md.readlink()
            if target.name == "AGENTS.md" or str(target).endswith("AGENTS.md"):
                # Use a separate file to avoid polluting AGENTS.md
                separate = project / ".claude" / "CLAUDE.md"
                separate.parent.mkdir(parents=True, exist_ok=True)
                if not separate.exists():
                    separate.write_text(
                        "# Claude-specific Instructions\n\n", encoding="utf-8"
                    )
                return separate
        return claude_md

    def inject_into_claude_md(
        self,
        package_id: str,
        project_dir: Path | str,
        content: str | None = None,
    ) -> Path:
        """Inject handoff context into project's CLAUDE.md.

        Args:
            package_id: Package ID to inject.
            project_dir: Project root directory.
            content: Optional custom injectable content. If None,
                generates default from package.

        Returns:
            Path to the CLAUDE.md file.
        """
        project = Path(project_dir)
        claude_md = self._resolve_claude_md(project)

        # Create CLAUDE.md if it doesn't exist
        if not claude_md.exists():
            claude_md.write_text("# Project Instructions\n\n", encoding="utf-8")

        text = claude_md.read_text(encoding="utf-8")

        # Remove existing handoff block
        text = self._remove_handoff_block(text)

        # Generate injectable content
        if content is None:
            content = self._generate_injectable_markdown(package_id)

        inject_block = f"\n{HANDOFF_BLOCK_START}\n{content}\n{HANDOFF_BLOCK_END}\n"

        # Append to end of file
        new_text = text.rstrip() + "\n" + inject_block
        claude_md.write_text(new_text, encoding="utf-8")

        return claude_md

    def cleanup_claude_md(self, project_dir: Path | str) -> bool:
        """Remove handoff injection block from CLAUDE.md.

        Args:
            project_dir: Project root directory.

        Returns:
            True if a block was removed.
        """
        project = Path(project_dir)
        claude_md = self._resolve_claude_md(project)

        if not claude_md.exists():
            return False

        text = claude_md.read_text(encoding="utf-8")
        new_text = self._remove_handoff_block(text)

        if new_text != text:
            claude_md.write_text(new_text, encoding="utf-8")
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
        """Generate a markdown block for injection into CLAUDE.md."""
        return f"""## Handoff Context

You are resuming work from a previous agent session.

- **Package ID**: `{package_id}`
- **Status**: Pending resume

When you begin working, review the progress summary and continue from the indicated next step.
"""

    def generate_hooks_config(self) -> dict[str, Any]:
        """Generate Claude Code hooks configuration for handoff.

        Returns:
            Hooks config dict for .claude/settings.local.json
        """
        return {
            "hooks": {
                "Stop": [
                    {
                        "command": "handoff-relay hook session-stop",
                        "description": "Auto-save handoff on session end",
                    }
                ],
            }
        }

    def write_hooks_config(self, project_dir: Path | str) -> Path:
        """Write hooks config to .claude/settings.local.json.

        Args:
            project_dir: Project root directory.

        Returns:
            Path to the settings file.
        """
        project = Path(project_dir)
        claude_dir = project / ".claude"
        claude_dir.mkdir(exist_ok=True)

        settings_path = claude_dir / "settings.local.json"
        config = self.generate_hooks_config()

        import json
        settings_path.write_text(
            json.dumps(config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return settings_path

    def generate_handoff_command(self) -> str:
        """Generate the /handoff slash command content for Claude Code.

        Returns:
            Markdown content for .claude/commands/handoff.md
        """
        return """# /handoff - Create context handoff package

## Description
Create a structured handoff package so another agent can continue this work.

## Instructions
1. Ask the user for confirmation: "Shall I create a handoff package for the current task?"
2. Determine the task ID from context (current branch name, issue number, or ask user).
3. Call the `handoff_create_package` MCP tool with:
   - `source_agent`: "claude-code"
   - `task_id`: the identified task ID
   - `reason`: "token_limit", "user_triggered", "error_recovery", "capability_mismatch", or "scheduled"
   - `notes`: brief summary of completed work and next steps
4. Report the package ID to the user.
5. If MCP tools are unavailable, write a handoff summary to `handoff-brief.md` in the project root.
"""

    def write_handoff_command(self, project_dir: Path | str) -> Path:
        """Write /handoff command to .claude/commands/handoff.md.

        Args:
            project_dir: Project root directory.

        Returns:
            Path to the command file.
        """
        project = Path(project_dir)
        commands_dir = project / ".claude" / "commands"
        commands_dir.mkdir(parents=True, exist_ok=True)

        cmd_path = commands_dir / "handoff.md"
        cmd_path.write_text(self.generate_handoff_command(), encoding="utf-8")
        return cmd_path
