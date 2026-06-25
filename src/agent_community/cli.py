"""Agent Community CLI — orchestrate multi-agent coding runs.

Commands:
    run       Decompose a task and execute with multiple agents
    plan      Decompose a task without executing (preview only)
    cleanup   Remove all community worktrees
    status    Show available agents and their profiles
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
        "CLI requires 'typer'. Install with: pip install agent-community[cli]"
    ) from exc

from agent_community.models import AgentType
from agent_community.orchestrator import CommunityOrchestrator
from agent_community.planner.decomposer import TaskDecomposer
from agent_community.router.agent_registry import AgentRegistry

app = typer.Typer(
    name="agent-community",
    help="Agent Local Community — intelligent multi-agent coding orchestration",
    add_completion=False,
)


@app.command()
def run(
    task: str = typer.Argument(..., help="Task description to execute"),
    project_dir: Path = typer.Option(
        Path.cwd(), "--project-dir", "-d", help="Project directory"
    ),
    agents: str = typer.Option(
        "", "--agents", "-a",
        help="Comma-separated agent list (claude-code,codex-cli). Default: all available.",
    ),
    max_concurrent: int = typer.Option(
        3, "--max-concurrent", "-c", help="Max parallel agents"
    ),
    max_turns: int = typer.Option(
        20, "--max-turns", help="Max turns per agent"
    ),
    timeout: int = typer.Option(
        600, "--timeout", help="Timeout per agent (seconds)"
    ),
    validate_cmd: str = typer.Option(
        "pytest -x -q", "--validate", help="Post-merge validation command"
    ),
    skip_validation: bool = typer.Option(
        False, "--skip-validation", help="Skip post-merge validation"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Decompose and route without executing"
    ),
    output_json: bool = typer.Option(
        False, "--json", help="Output result as JSON"
    ),
) -> None:
    """Decompose a task and execute with multiple agents in parallel."""
    allowed = _parse_agents(agents)

    orchestrator = CommunityOrchestrator(
        project_dir=project_dir,
        allowed_agents=allowed,
        max_concurrent=max_concurrent,
        max_turns=max_turns,
        timeout_seconds=timeout,
        validate_command=validate_cmd,
        skip_validation=skip_validation,
        dry_run=dry_run,
    )

    result = asyncio.run(orchestrator.run(task))

    if output_json:
        _print_result_json(result)
    else:
        _print_result_summary(result)

    if result.plan and result.plan.has_failures():
        raise typer.Exit(1)


@app.command()
def plan(
    task: str = typer.Argument(..., help="Task to decompose"),
    project_dir: Path = typer.Option(
        Path.cwd(), "--project-dir", "-d", help="Project directory"
    ),
) -> None:
    """Decompose a task into sub-tasks without executing (preview only)."""
    decomposer = TaskDecomposer()
    result = asyncio.run(decomposer.decompose(task, project_dir))

    typer.echo(f"\n📋 Task Plan: {result.plan_id}")
    typer.echo(f"   Suitable for parallel: {result.suitable_for_parallel}")

    if not result.suitable_for_parallel:
        typer.echo(f"   Reason: {result.reason_if_not_suitable}")
        return

    registry = AgentRegistry()
    typer.echo(f"\n   Sub-tasks ({len(result.subtasks)}):\n")

    for i, st in enumerate(result.subtasks):
        agent = registry.route_task(st)
        deps = f" → depends on: {st.depends_on}" if st.depends_on else ""
        typer.echo(f"   [{i+1}] {st.title}  (agent: {agent.value})")
        typer.echo(f"       {st.description[:100]}...")
        typer.echo(f"       Creates: {st.files_creates}")
        typer.echo(f"       Modifies: {st.files_modifies}{deps}")
        typer.echo(f"       Branch: {st.branch_name}")
        typer.echo("")

    conflicts = result.validate_file_ownership()
    if conflicts:
        typer.echo("⚠️  File ownership conflicts:")
        for c in conflicts:
            typer.echo(f"   {c}")
    else:
        typer.echo("✓ No file ownership conflicts")


@app.command()
def cleanup(
    project_dir: Path = typer.Option(
        Path.cwd(), "--project-dir", "-d", help="Project directory"
    ),
) -> None:
    """Remove all community worktrees."""
    from agent_community.executor.worktree_mgr import WorktreeManager
    mgr = WorktreeManager(project_dir)
    count = mgr.cleanup_all()
    typer.echo(f"Cleaned up {count} worktrees.")


@app.command()
def status() -> None:
    """Show available agents and their capability profiles."""
    registry = AgentRegistry()

    typer.echo("\n🤖 Agent Community — Available Agents\n")

    for profile in registry.available_agents():
        typer.echo(f"  {profile.agent_type.value}")
        typer.echo(f"    Binary: {profile.binary_name}")
        typer.echo(f"    Strengths: {', '.join(profile.strengths)}")
        typer.echo(f"    Weaknesses: {', '.join(profile.weaknesses)}")
        typer.echo(f"    Max concurrent: {profile.max_concurrent}")
        typer.echo(f"    Cost/1k tokens: ${profile.cost_per_1k_tokens:.4f}")
        typer.echo("")

    # Check which agents are actually installed
    import shutil
    typer.echo("  Installation status:")
    for agent_type in AgentType:
        profile = registry._profiles.get(agent_type)
        if profile:
            installed = shutil.which(profile.binary_name) is not None
            status = "✓ installed" if installed else "✗ not found"
            typer.echo(f"    {agent_type.value}: {status}")
    typer.echo("")


# --- Helpers ---

def _parse_agents(agents_str: str) -> list[AgentType] | None:
    """Parse comma-separated agent list."""
    if not agents_str:
        return None
    parts = [s.strip() for s in agents_str.split(",") if s.strip()]
    result = []
    for part in parts:
        try:
            result.append(AgentType(part))
        except ValueError:
            typer.echo(f"Unknown agent: {part}", err=True)
            typer.echo(f"Available: {[a.value for a in AgentType]}")
            raise typer.Exit(1)
    return result


def _print_result_summary(result: Any) -> None:
    """Print a human-readable summary."""
    typer.echo("")
    typer.echo("=" * 60)
    typer.echo(f"  Community Run: {result.run_id}")
    typer.echo("=" * 60)

    if result.plan:
        typer.echo(f"\n  Plan: {len(result.plan.subtasks)} sub-tasks")
        for st in result.plan.subtasks:
            icon = "✓" if st.status.value == "completed" else "✗" if st.status.value == "failed" else "○"
            agent = st.assigned_agent.value if st.assigned_agent else "?"
            typer.echo(f"    {icon} {st.title} ({agent}) — {st.status.value}")
            if st.result_summary:
                typer.echo(f"      {st.result_summary[:120]}...")

    typer.echo(f"\n  Merge: {'✓ success' if result.merge_success else '✗ conflicts' if result.merge_conflicts else 'skipped'}")
    if result.merge_conflicts:
        for c in result.merge_conflicts:
            typer.echo(f"    {c}")

    if result.validation_passed is not None:
        typer.echo(f"  Validation: {'✓ passed' if result.validation_passed else '✗ failed'}")

    typer.echo(f"\n  Total cost: ${result.total_cost_usd:.4f}")
    typer.echo(f"  Wall time: {result.total_duration_seconds:.1f}s")
    typer.echo("")


def _print_result_json(result: Any) -> None:
    """Print result as JSON."""
    data = {
        "run_id": result.run_id,
        "task": result.original_task,
        "subtasks": [
            {
                "task_id": st.task_id,
                "title": st.title,
                "agent": st.assigned_agent.value if st.assigned_agent else None,
                "status": st.status.value,
                "cost_usd": st.cost_usd,
            }
            for st in (result.plan.subtasks if result.plan else [])
        ],
        "merge_success": result.merge_success,
        "merge_conflicts": result.merge_conflicts,
        "validation_passed": result.validation_passed,
        "total_cost_usd": result.total_cost_usd,
        "total_duration_seconds": result.total_duration_seconds,
    }
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
