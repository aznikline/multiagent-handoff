"""HandoffRelayService — unified service layer for handoff operations.

All CLI commands and MCP tools delegate to this service. Extracting logic here
eliminates duplication between interfaces and makes operations directly testable
without spinning up servers or parsing CLI arguments.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from handoff.models.package import ContextPackage, PackageMeta, SourceInfo
from handoff.models.task import HandoffReason, ProgressSummary, TaskInfo

from handoff_relay._builders import (
    build_capture_package,
    format_injectable,
    format_system_prompt_addition,
    generate_brief_md_from_dict,
)
from handoff_relay._utils import detect_current_agent, normalize_reason
from handoff_relay.adapters.claude_code import ClaudeCodeAdapter
from handoff_relay.adapters.codex import CodexAdapter
from handoff_relay.adapters.session_parser import get_parser
from handoff_relay.storage.local_store import LocalHandoffStore


class HandoffRelayService:
    """Service layer for handoff-relay operations.

    Each method is async and operates on the injected ``store``.  This class
    has no CLI or MCP dependencies — it is a pure service that can be tested
    in isolation.
    """

    def __init__(self, store: LocalHandoffStore | None = None) -> None:
        self._store = store or LocalHandoffStore()

    # ------------------------------------------------------------------ #
    # Package lifecycle
    # ------------------------------------------------------------------ #

    async def create_package(
        self,
        source_agent: str,
        task_id: str,
        reason: str,
        notes: str = "",
    ) -> dict[str, Any]:
        """Create a handoff package from the current session.

        Args:
            source_agent: Source agent type (claude-code, codex-cli, opencode).
            task_id: Task identifier.
            reason: Handoff reason string (legacy aliases are normalized).
            notes: Additional handoff notes.

        Returns:
            Package metadata dict with package_id, summary, file_path.
        """
        normalized_reason = HandoffReason(normalize_reason(reason))

        if source_agent == "claude-code":
            adapter = ClaudeCodeAdapter(store=self._store)
            result = await adapter.create_package(
                task_id=task_id,
                reason=normalized_reason,
                notes=notes,
            )
            return {
                "package_id": result["package_id"],
                "summary": result["summary"],
                "file_path": result["file_path"],
            }

        # Generic path: parse session and create package
        parser = get_parser(source_agent)
        snapshot = parser.parse()

        package = ContextPackage(
            meta=PackageMeta(
                source=SourceInfo(agent_id=source_agent),
                handoff_reason=normalized_reason,
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

        await self._store.save(package)

        return {
            "package_id": package.meta.package_id,
            "summary": package.task.progress_summary.to_markdown(),
            "file_path": str(self._store._package_path(package.meta.package_id)),
        }

    async def get_package(
        self,
        package_id: str,
        format: str = "summary",
    ) -> dict[str, Any]:
        """Retrieve a handoff package in the requested format.

        Args:
            package_id: Package ID to retrieve.
            format: Output format — ``full``, ``summary``, or ``injectable``.

        Returns:
            Package data dict, or ``{"error": ...}`` if not found.
        """
        package = await self._store.load(package_id)
        if package is None:
            return {"error": f"Package not found: {package_id}"}

        if format == "full":
            return {"package": package.model_dump()}
        elif format == "injectable":
            return {
                "injectable_markdown": format_injectable(package),
                "system_prompt_addition": format_system_prompt_addition(package),
            }
        else:
            return {
                "summary": package.task.progress_summary.to_markdown(),
                "package_id": package_id,
                "status": "stored",
            }

    async def list_packages(
        self,
        status: str | None = None,
        source_agent: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List handoff packages with optional filtering.

        Returns:
            Dict with ``packages`` list and ``count``.
        """
        packages = await self._store.list_packages(
            status=status,
            source_agent=source_agent,
            limit=limit,
        )
        return {"packages": packages, "count": len(packages)}

    async def capture_state(
        self,
        agent_type: str,
        messages: list[dict[str, Any]],
        variables: dict[str, Any],
        current_step: str,
        blockers: list[str],
    ) -> dict[str, Any]:
        """Capture the current agent session state.

        Returns:
            Capture metadata with capture_id, estimated_token_count, status.
        """
        import json
        import uuid

        capture_id = f"capture-{uuid.uuid4().hex[:8]}"
        package = build_capture_package(
            capture_id=capture_id,
            agent_type=agent_type,
            messages=messages,
            variables=variables,
            current_step=current_step,
            blockers=blockers,
        )
        await self._store.save(package)

        payload_size = len(json.dumps(messages)) + len(json.dumps(variables))
        estimated_tokens = payload_size // 4

        return {
            "capture_id": capture_id,
            "estimated_token_count": estimated_tokens,
            "status": "captured",
            "package_id": capture_id,
        }

    async def get_injectable_context(
        self,
        package_id: str,
        target_agent: str,
        max_tokens: int = 2000,
    ) -> dict[str, Any]:
        """Get handoff context formatted for injection into target agent.

        Returns:
            Injectable markdown, system prompt addition, and metadata,
            or ``{"error": ...}`` if not found.
        """
        package = await self._store.load(package_id)
        if package is None:
            return {"error": f"Package not found: {package_id}"}

        injectable = format_injectable(package)
        # Rough truncation to token budget
        if len(injectable) > max_tokens * 4:
            injectable = injectable[: max_tokens * 4] + "\n\n...[truncated]"

        return {
            "injectable_markdown": injectable,
            "system_prompt_addition": format_system_prompt_addition(package),
            "target_agent": target_agent,
            "package_id": package_id,
        }

    async def cleanup_expired(self) -> int:
        """Remove expired packages.

        Returns:
            Number of packages removed.
        """
        return await self._store.cleanup_expired()

    async def switch(
        self,
        target_agent: str,
        task_id: str | None = None,
        notes: str = "",
        project_dir: Path | str | None = None,
        source_agent: str | None = None,
    ) -> dict[str, Any]:
        """Switch from the current agent to a target agent.

        Auto-detects the currently active agent, creates a handoff package,
        injects context into the target agent's config, and returns launch
        instructions.

        Args:
            target_agent: Target agent type ("claude-code", "codex-cli", "opencode").
            task_id: Task identifier. If None, auto-generated from source agent.
            notes: Additional handoff notes.
            project_dir: Project directory for context injection.
            source_agent: Optional explicit source agent override. If None,
                auto-detected from session directories.

        Returns:
            Switch result dict with package_id, source_agent, target_agent,
            injected_path, and launch_command.
        """
        if source_agent is None:
            source_agent = detect_current_agent()
        if source_agent is None:
            return {"error": "No active agent session detected."}

        if source_agent == target_agent:
            return {"error": f"Source and target agent are the same: {target_agent}"}

        resolved_task_id = task_id or f"switch-{source_agent}-to-{target_agent}"

        # Create handoff package from source agent
        pkg_result = await self.create_package(
            source_agent=source_agent,
            task_id=resolved_task_id,
            reason="capability_mismatch",
            notes=notes,
        )
        package_id = pkg_result["package_id"]

        # Inject context into target agent's config
        project = Path(project_dir) if project_dir else Path.cwd()
        injected_path: Path | None = None

        if target_agent == "claude-code":
            adapter = ClaudeCodeAdapter()
            injected_path = adapter.inject_into_claude_md(package_id, project)
        elif target_agent == "codex-cli":
            adapter = CodexAdapter()
            injected_path = adapter.inject_into_agents_md(package_id, project)
        elif target_agent == "opencode":
            # Generic: write handoff-brief.md
            brief_path = project / "handoff-brief.md"
            pkg = await self.get_package(package_id, format="full")
            if "package" in pkg:
                brief_path.write_text(
                    generate_brief_md_from_dict(pkg["package"]),
                    encoding="utf-8",
                )
            injected_path = brief_path

        # Build launch command
        if target_agent == "claude-code":
            launch_cmd = f"cd {project} && claude"
        elif target_agent == "codex-cli":
            launch_cmd = f"cd {project} && codex"
        elif target_agent == "opencode":
            launch_cmd = f"cd {project} && opencode"
        else:
            launch_cmd = f"cd {project} && <{target_agent}>"

        return {
            "package_id": package_id,
            "source_agent": source_agent,
            "target_agent": target_agent,
            "task_id": resolved_task_id,
            "injected_path": str(injected_path) if injected_path else None,
            "project_dir": str(project),
            "launch_command": launch_cmd,
        }

    # ------------------------------------------------------------------ #
    # Project-level operations (not package-centric)
    # ------------------------------------------------------------------ #

    def inject_into_claude_md(
        self,
        package_id: str,
        project_dir: Path | str,
    ) -> Path:
        """Inject handoff context into project's CLAUDE.md.

        Returns:
            Path to the CLAUDE.md file that was written.
        """
        adapter = ClaudeCodeAdapter()
        return adapter.inject_into_claude_md(package_id, project_dir)

    def cleanup_claude_md(self, project_dir: Path | str) -> bool:
        """Remove handoff injection block from CLAUDE.md.

        Returns:
            True if a block was removed.
        """
        adapter = ClaudeCodeAdapter()
        return adapter.cleanup_claude_md(project_dir)
