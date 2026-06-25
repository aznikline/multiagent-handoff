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

from handoff_relay._builders import generate_brief_md_from_dict
from handoff_relay._utils import is_inside_tmux, write_switch_marker
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
    typer.echo("")
    typer.echo("Next step for seamless switching:")
    typer.echo("  handoff-relay install-shell-hook")
    typer.echo("  # Then restart your terminal or run: source ~/.zshrc  (or ~/.bashrc)")


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
        content = generate_brief_md_from_dict(package)
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
def switch(
    target: str = typer.Argument(..., help="Target agent (claude-code, codex-cli, opencode)"),
    task: str = typer.Option(
        "", "--task", "-t",
        help="Task identifier (auto-generated if omitted)",
    ),
    notes: str = typer.Option(
        "", "--notes", "-n",
        help="Additional notes about current progress",
    ),
    project_dir: Path = typer.Option(
        Path.cwd(), "--project-dir", "-d",
        help="Project directory",
    ),
) -> None:
    """Switch from current agent to target agent with context handoff."""
    service = _get_service()
    result = asyncio.run(service.switch(
        target_agent=target,
        task_id=task or None,
        notes=notes,
        project_dir=project_dir,
    ))

    if "error" in result:
        typer.echo(f"Error: {result['error']}", err=True)
        raise typer.Exit(1)

    typer.echo(f"✓ Created handoff package: {result['package_id']}")
    typer.echo(f"  From: {result['source_agent']} → To: {result['target_agent']}")
    typer.echo(f"  Task: {result['task_id']}")

    if result.get("injected_path"):
        typer.echo(f"✓ Injected context into {result['injected_path']}")

    project = Path(project_dir)

    if is_inside_tmux():
        # Tmux path: kill-recreate target window so the agent boots fresh
        # and reads the latest injected context.
        try:
            _tmux_switch(target, project)
        except Exception as exc:
            typer.echo(f"tmux switch failed: {exc}", err=True)
            raise typer.Exit(1)

        typer.echo("")
        typer.echo(f"Switched to {target} in tmux.")
        typer.echo("Your screen should now show the target agent.")
    else:
        # Shell-hook path: write marker for precmd auto-exec
        marker_path = write_switch_marker(result["launch_command"])
        typer.echo("")
        typer.echo(f"Switch marker written to {marker_path}")
        typer.echo("")
        typer.echo("To complete the switch:")
        typer.echo("  1. Exit the current agent (Ctrl+D or /quit)")
        typer.echo(f"  2. Your shell will automatically exec {result['target_agent']}")
        typer.echo("")
        typer.echo("If auto-switch doesn't work, run: handoff-relay install-shell-hook")


@app.command()
def install_shell_hook(
    shell: str = typer.Option(
        "auto", "--shell", "-s",
        help="Shell type (auto, zsh, bash)",
    ),
    uninstall: bool = typer.Option(
        False, "--uninstall", "-u",
        help="Remove the hook instead of installing",
    ),
) -> None:
    """Install the shell precmd hook for auto-switching agents.

    The hook checks ~/.handoff/switch_cmd on every prompt and evals it,
    which for an ``exec`` command replaces the shell with the target CLI.
    """
    import os

    # Detect shell
    if shell == "auto":
        shell_path = os.environ.get("SHELL", "")
        if "zsh" in shell_path:
            shell = "zsh"
        elif "bash" in shell_path:
            shell = "bash"
        else:
            typer.echo(f"Could not auto-detect shell from SHELL={shell_path}", err=True)
            typer.echo("Please specify with --shell zsh or --shell bash")
            raise typer.Exit(1)

    if shell == "zsh":
        rc_file = Path.home() / ".zshrc"
        hook_body = _ZSH_HOOK
    elif shell == "bash":
        rc_file = Path.home() / ".bashrc"
        hook_body = _BASH_HOOK
    else:
        typer.echo(f"Unsupported shell: {shell}", err=True)
        raise typer.Exit(1)

    if not rc_file.exists():
        typer.echo(f"Shell config not found: {rc_file}", err=True)
        raise typer.Exit(1)

    text = rc_file.read_text(encoding="utf-8")

    # Remove existing hook block
    text = _remove_hook_block(text)

    if uninstall:
        rc_file.write_text(text, encoding="utf-8")
        typer.echo(f"Removed handoff-relay hook from {rc_file}")
        typer.echo("Run `source {rc_file}` or restart your terminal to apply.")
        return

    # Append new hook block
    new_text = text.rstrip() + "\n\n" + hook_body + "\n"
    rc_file.write_text(new_text, encoding="utf-8")

    typer.echo(f"✓ Installed handoff-relay hook into {rc_file}")
    typer.echo("")
    typer.echo("How it works:")
    typer.echo("  1. Run 'handoff-relay switch codex-cli' inside Claude Code")
    typer.echo("  2. Exit Claude Code (Ctrl+D)")
    typer.echo("  3. Your shell prompt will automatically exec codex")
    typer.echo("")
    typer.echo(f"Run `source {rc_file}` or restart your terminal to activate.")


_HOOK_START = "# <<< handoff-relay hook (begin) >>>"
_HOOK_END = "# <<< handoff-relay hook (end) >>>"

_ZSH_HOOK = f"""{_HOOK_START}
handoff_precmd() {{
    local marker="$HOME/.handoff/switch_cmd"
    if [[ -f "$marker" ]]; then
        local cmd
        cmd=$(cat "$marker")
        rm -f "$marker"
        eval "$cmd"
    fi
}}
autoload -U add-zsh-hook
add-zsh-hook precmd handoff_precmd
{_HOOK_END}"""

_BASH_HOOK = f"""{_HOOK_START}
__handoff_check() {{
    local marker="$HOME/.handoff/switch_cmd"
    if [[ -f "$marker" ]]; then
        local cmd
        cmd=$(cat "$marker")
        rm -f "$marker"
        eval "$cmd"
    fi
}}
if [[ -z "$PROMPT_COMMAND" ]]; then
    PROMPT_COMMAND='__handoff_check'
else
    PROMPT_COMMAND='__handoff_check; '"$PROMPT_COMMAND"
fi
{_HOOK_END}"""


def _agent_binary(agent_type: str) -> str:
    """Map agent type to CLI binary name."""
    return {
        "claude-code": "claude",
        "codex-cli": "codex",
        "opencode": "opencode",
    }.get(agent_type, agent_type)


def _tmux_window_exists(window_name: str) -> bool:
    """Check if a tmux window exists in the current session."""
    import subprocess

    result = subprocess.run(
        ["tmux", "list-windows", "-F", "#W"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    existing = [w.strip() for w in result.stdout.split("\n") if w.strip()]
    return window_name in existing


def _tmux_switch(target_agent: str, project_dir: Path) -> None:
    """Kill and recreate target tmux window with a fresh agent process.

    This ensures the target agent always boots from scratch and reads the
    latest injected context (AGENTS.md / CLAUDE.md).
    """
    import subprocess
    import time

    window_name = target_agent.replace("-cli", "").replace("-code", "")
    binary = _agent_binary(target_agent)

    # Kill existing window to force fresh start
    if _tmux_window_exists(window_name):
        subprocess.run(
            ["tmux", "kill-window", "-t", window_name],
            check=False,
        )
        time.sleep(0.3)

    # Create new window with fresh agent
    subprocess.run(
        [
            "tmux",
            "new-window",
            "-n",
            window_name,
            "-c",
            str(project_dir),
            binary,
        ],
        check=True,
    )

    # Switch to it immediately
    subprocess.run(
        ["tmux", "select-window", "-t", window_name],
        check=True,
    )


def _remove_hook_block(text: str) -> str:
    """Remove an existing handoff-relay hook block from shell rc text."""
    while True:
        start = text.find(_HOOK_START)
        if start == -1:
            break
        end = text.find(_HOOK_END, start)
        if end == -1:
            break
        end += len(_HOOK_END)
        text = text[:start].rstrip() + text[end:].lstrip()
    return text


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


def main() -> None:
    """CLI entry point."""
    app()


if __name__ == "__main__":
    main()
