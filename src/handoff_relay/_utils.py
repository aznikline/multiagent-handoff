"""Shared utilities for handoff-relay."""

from __future__ import annotations

from pathlib import Path


# Maps legacy/alias reason values to valid HandoffReason enum values.
# Keep in sync with HandoffReason enum in handoff.models.task.
_REASON_ALIASES: dict[str, str] = {
    "manual": "user_triggered",
    "rate_limit": "user_triggered",
    "error": "error_recovery",
}

#: Marker file written by ``switch``; shell hook evals it on next prompt.
_SWITCH_MARKER: Path = Path.home() / ".handoff" / "switch_cmd"


def normalize_reason(reason: str) -> str:
    """Map legacy/alias reason values to valid HandoffReason enum values.

    Args:
        reason: Raw reason string from user input or agent command.

    Returns:
        Normalized reason string valid for HandoffReason.
    """
    return _REASON_ALIASES.get(reason, reason)


def detect_current_agent() -> str | None:
    """Auto-detect the currently active CLI agent.

    Compares the most recently modified session file across known agent
    session directories. Returns the agent with the newest session activity.

    Returns:
        Agent type string ("claude-code", "codex-cli", "opencode") or
        ``None`` if no agent sessions are found.
    """
    session_dirs: dict[str, Path] = {
        "claude-code": Path.home() / ".claude" / "sessions",
        "codex-cli": Path.home() / ".codex" / "sessions",
        "opencode": Path.home() / ".local" / "share" / "opencode" / "sessions",
    }

    newest_mtime: float = 0.0
    newest_agent: str | None = None

    for agent, session_dir in session_dirs.items():
        if not session_dir.exists():
            continue

        latest_mtime: float = 0.0
        for f in session_dir.rglob("*"):
            if f.is_file():
                mtime = f.stat().st_mtime
                if mtime > latest_mtime:
                    latest_mtime = mtime

        if latest_mtime > newest_mtime:
            newest_mtime = latest_mtime
            newest_agent = agent

    return newest_agent


def write_switch_marker(launch_command: str) -> Path:
    """Write a shell marker file that the shell hook will eval.

    The shell ``precmd`` / ``PROMPT_COMMAND`` hook checks for this file on
    every prompt.  When found it reads the command, deletes the marker, and
    ``eval``'s it — which for an ``exec`` command replaces the shell process
    with the target CLI.

    Args:
        launch_command: Shell command to execute on next prompt.

    Returns:
        Path to the written marker file.
    """
    _SWITCH_MARKER.parent.mkdir(mode=0o700, exist_ok=True, parents=True)
    _SWITCH_MARKER.write_text(launch_command, encoding="utf-8")
    return _SWITCH_MARKER


def read_and_clear_switch_marker() -> str | None:
    """Read and atomically remove the switch marker.

    Returns:
        The command string if the marker existed, otherwise ``None``.
    """
    if not _SWITCH_MARKER.exists():
        return None
    cmd = _SWITCH_MARKER.read_text(encoding="utf-8")
    _SWITCH_MARKER.unlink(missing_ok=True)
    return cmd


def has_switch_marker() -> bool:
    """Return whether a switch marker is currently pending."""
    return _SWITCH_MARKER.exists()


def is_inside_tmux() -> bool:
    """Detect whether the current process is running inside a tmux session.

    Checks the ``TMUX`` environment variable which tmux sets on every
    pane/window in the session.

    Returns:
        ``True`` if inside tmux, ``False`` otherwise.
    """
    return "TMUX" in __import__("os").environ
