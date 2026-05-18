"""Handoff Relay CLI - Local CLI Agent context handoff coordinator.

Commands:
    init          Initialize handoff configuration for a project
    create        Create a handoff package from current session
    list          List handoff packages
    show          Show package details
    inject        Generate injectable context for target agent
    cleanup       Remove expired packages
    serve         Start MCP server
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

try:
    import typer
except ImportError as exc:
    raise ImportError(
        "CLI requires 'typer'. Install with: pip install agent-context-handoff[cli]"
    ) from exc

from handoff.models.task import HandoffReason

from handoff_relay.adapters.claude_code import ClaudeCodeAdapter
from handoff_relay.service import HandoffRelayService
from handoff_relay.storage.local_store import LocalHandoffStore

app = typer.Typer(
    name="handoff-relay",
    help="Local CLI Agent context handoff coordinator",
    add_completion=False,
)


def _get_service() -> HandoffRelayService:
    """Get or create the handoff relay service."""
    return HandoffRelayService(store=LocalHandoffStore())


@app.command()
def init(
    project_dir: Path = typer.Option(
        Path.cwd(), "--project-dir", "-d", help="Project directory"
    ),
    agent: str = typer.Option(
        "claude-code", "--agent", "-a",
        help="Primary agent type (claude-code, codex-cli, opencode). "
             "Claude Code has the richest support (hooks, injection). "
             "Codex and OpenCode use generic session parsing.",
    ),
) -> None:
    """Initialize handoff configuration for a project."""
    project = Path(project_dir)
    typer.echo(f"Initializing handoff for {project.absolute()}")

    # Create AGENTS.md if it doesn't exist
    agents_md = project / "AGENTS.md"
    if not agents_md.exists():
        agents_md.write_text(
            _default_agents_md(agent), encoding="utf-8"
        )
        typer.echo(f"  Created {agents_md}")

    # Create CLAUDE.md symlink if needed
    claude_md = project / "CLAUDE.md"
    if not claude_md.exists() and agent in ("claude-code", "any"):
        if sys.platform == "win32":
            claude_md.write_text(agents_md.read_text(), encoding="utf-8")
        else:
            claude_md.symlink_to("AGENTS.md")
        typer.echo(f"  Created {claude_md}")

    # Setup Claude Code specific files
    if agent in ("claude-code", "any"):
        adapter = ClaudeCodeAdapter()
        adapter.write_handoff_command(project)
        typer.echo("  Created .claude/commands/handoff.md")
        adapter.write_hooks_config(project)
        typer.echo("  Created .claude/settings.local.json")

    # Create .handoff directory
    handoff_dir = Path.home() / ".handoff"
    handoff_dir.mkdir(mode=0o700, exist_ok=True)
    typer.echo(f"  Created {handoff_dir}")

    typer.echo("Done. Add your project-specific instructions to AGENTS.md.")


@app.command()
def create(
    source: str = typer.Option(
        ..., "--source", "-s",
        help="Source agent (claude-code, codex-cli, opencode)",
    ),
    task: str = typer.Option(
        ..., "--task", "-t",
        help="Task identifier",
    ),
    reason: str = typer.Option(
        "user_triggered", "--reason", "-r",
        help="Handoff reason (token_limit, user_triggered, error_recovery, capability_mismatch, scheduled). "
             "Legacy aliases: manual → user_triggered, error → error_recovery.",
    ),
    notes: str = typer.Option(
        "", "--notes", "-n",
        help="Additional notes",
    ),
) -> None:
    """Create a handoff package from the current session."""
    service = _get_service()
    result = asyncio.run(service.create_package(
        source_agent=source,
        task_id=task,
        reason=reason,
        notes=notes,
    ))
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))


@app.command()
def list(
    status: str = typer.Option(None, "--status", help="Filter by status"),
    source: str = typer.Option(None, "--source", "-s", help="Filter by source agent"),
    limit: int = typer.Option(20, "--limit", "-l", help="Max results"),
) -> None:
    """List handoff packages."""
    service = _get_service()
    result = asyncio.run(service.list_packages(
        status=status,
        source_agent=source,
        limit=limit,
    ))
    packages = result["packages"]

    if not packages:
        typer.echo("No packages found.")
        return

    for pkg in packages:
        typer.echo(
            f"{pkg['package_id']} | {pkg['source_agent']} | "
            f"{pkg['status']} | {pkg['task_id']} | {pkg['created_at']}"
        )


@app.command()
def show(
    package_id: str = typer.Argument(..., help="Package ID to show"),
) -> None:
    """Show package details."""
    service = _get_service()
    result = asyncio.run(service.get_package(package_id, format="full"))

    if "error" in result:
        typer.echo(result["error"], err=True)
        raise typer.Exit(1)

    typer.echo(json.dumps(result["package"], indent=2, ensure_ascii=False, default=str))


@app.command()
def inject(
    package_id: str = typer.Argument(..., help="Package ID to inject"),
    target: str = typer.Option(
        ..., "--target", "-t",
        help="Target agent (claude-code, codex-cli, opencode)",
    ),
    project_dir: Path = typer.Option(
        Path.cwd(), "--project-dir", "-d",
        help="Project directory",
    ),
) -> None:
    """Generate injectable context for target agent."""
    project = Path(project_dir)

    if target == "claude-code":
        adapter = ClaudeCodeAdapter()
        path = adapter.inject_into_claude_md(package_id, project)
        typer.echo(f"Injected into {path}")
    else:
        # Generic: generate handoff-brief.md
        service = _get_service()
        result = asyncio.run(service.get_package(package_id))
        if "error" in result:
            typer.echo(result["error"], err=True)
            raise typer.Exit(1)

        package = result.get("package")
        if package is None:
            typer.echo(f"Package not found: {package_id}", err=True)
            raise typer.Exit(1)

        brief_path = project / "handoff-brief.md"
        content = _generate_brief_md_from_dict(package)
        brief_path.write_text(content, encoding="utf-8")
        typer.echo(f"Generated {brief_path}")


@app.command()
def cleanup(
    older_than_days: int = typer.Option(
        7, "--older-than", "-d",
        help="Remove packages older than N days",
    ),
) -> None:
    """Remove expired handoff packages."""
    service = _get_service()
    count = asyncio.run(service.cleanup_expired())
    typer.echo(f"Cleaned {count} expired packages.")


@app.command()
def hook(
    event: str = typer.Argument(..., help="Hook event (session-stop, session-start, post-tool-use)"),
    project_dir: Path = typer.Option(
        Path.cwd(), "--project-dir", "-d",
        help="Project directory",
    ),
) -> None:
    """Handle Claude Code / agent lifecycle hooks."""
    service = _get_service()

    if event == "session-stop":
        # Auto-create a handoff package from the latest session
        adapter = ClaudeCodeAdapter(store=service._store)
        result = asyncio.run(adapter.create_package(
            task_id="auto-session",
            reason=HandoffReason.USER_TRIGGERED,
            notes="Auto-captured on session end via Stop hook",
        ))
        typer.echo(f"Auto-saved handoff: {result['package_id']}")
    elif event == "session-start":
        # Check for pending handoff packages
        result = asyncio.run(service.list_packages(status="pending", limit=5))
        packages = result["packages"]
        if packages:
            typer.echo(f"Found {len(packages)} pending handoff package(s):")
            for pkg in packages:
                typer.echo(f"  - {pkg['package_id']} ({pkg['source_agent']})")
        else:
            typer.echo("No pending handoff packages.")
    elif event == "post-tool-use":
        typer.echo("Post-tool-use hook acknowledged (no action).")
    else:
        typer.echo(f"Unknown hook event: {event}", err=True)
        raise typer.Exit(1)


@app.command()
def serve(
    mcp: bool = typer.Option(True, "--mcp/--no-mcp", help="Expose MCP tools"),
) -> None:
    """Start the handoff-relay server."""
    if mcp:
        from handoff_relay.mcp_server import serve_mcp
        asyncio.run(serve_mcp())
    else:
        typer.echo("HTTP server mode not yet implemented. Use --mcp.")


def _default_agents_md(agent: str) -> str:
    """Generate default AGENTS.md content."""
    return f"""# Project Agent Instructions

## Handoff & Context Relay
- This project supports context handoff between agents via the Handoff Relay MCP server.
- When you reach token limits, encounter rate limiting, or are asked to hand off work:
  1. Run `/handoff` (or call `handoff_create_package` tool)
  2. Save the returned package ID
  3. Inform the user: "Handoff package created: {{package_id}}"
- When resuming work from another agent:
  1. The handoff context will be injected into your system prompt automatically.
  2. Look for `<handoff_context>` block in your instructions.
  3. Read the progress summary and continue from the indicated next step.

## Primary Agent
- Default agent: {agent}
"""


def _generate_brief_md_from_dict(package: dict[str, Any]) -> str:
    """Generate a handoff-brief.md for generic target agents."""
    task = package.get("task", {})
    ps = task.get("progress_summary", {})
    meta = package.get("meta", {})
    completed = ps.get("completed_steps", [])
    return f"""# Handoff Brief

## Task
{task.get("description", "N/A")}

## Progress Summary
- **Completed**: {', '.join(completed) if completed else 'N/A'}
- **Current Step**: {ps.get('current_step') or 'N/A'}
- **Key Results**: {ps.get('key_intermediate_results') or 'N/A'}
- **Blockers**: {ps.get('blockers') or 'N/A'}
- **Next Step**: {ps.get('next_expected_action') or 'N/A'}

## Package ID
`{meta.get('package_id', 'N/A')}`

<handoff_context>
You are resuming work from a previous agent session.
Review the progress summary above and continue from the indicated next step.
</handoff_context>
"""


def main() -> None:
    """CLI entry point."""
    app()


if __name__ == "__main__":
    main()
