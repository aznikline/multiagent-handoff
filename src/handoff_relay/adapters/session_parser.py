"""Parse session files from local CLI agents.

Reads conversation history and state from:
- Claude Code: ~/.claude/sessions/ (JSON)
- Codex CLI: ~/.codex/sessions/ (JSONL)
- OpenCode: ~/.local/share/opencode/sessions/ (JSON, experimental)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class SessionSnapshot:
    """Extracted session state from a CLI agent."""

    messages: list[dict[str, Any]]
    current_task: str
    last_user_message: str
    last_assistant_message: str
    state_variables: dict[str, Any]
    timestamp: datetime | None
    session_id: str | None


class ClaudeCodeSessionParser:
    """Parse Claude Code session files.

    Claude Code stores sessions in ~/.claude/sessions/ as JSON files.
    Format is implementation-defined; this parser is best-effort.
    """

    DEFAULT_SESSION_DIR = Path.home() / ".claude" / "sessions"

    def __init__(self, session_dir: Path | str | None = None) -> None:
        self._dir = Path(session_dir) if session_dir else self.DEFAULT_SESSION_DIR

    def find_latest_session(self) -> Path | None:
        """Find the most recently modified session file."""
        if not self._dir.exists():
            return None

        json_files = [
            f for f in self._dir.iterdir()
            if f.is_file() and f.suffix == ".json"
        ]
        if not json_files:
            return None

        return max(json_files, key=lambda f: f.stat().st_mtime)

    def parse(self, session_path: Path | str | None = None) -> SessionSnapshot:
        """Parse a Claude Code session file."""
        path = Path(session_path) if session_path else self.find_latest_session()
        if path is None or not path.exists():
            return _empty_snapshot()

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        messages: list[dict[str, Any]] = []
        if isinstance(data, list):
            messages = self._normalize_messages(data)
        elif isinstance(data, dict):
            for key in ("messages", "conversation", "history", "turns"):
                if key in data and isinstance(data[key], list):
                    messages = self._normalize_messages(data[key])
                    break

        return _build_snapshot(messages, path)

    def _normalize_messages(self, raw: list[Any]) -> list[dict[str, Any]]:
        """Normalize various message formats to standard dicts."""
        normalized: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, dict):
                msg = {
                    "role": item.get("role", item.get("speaker", "unknown")),
                    "content": item.get("content", item.get("text", item.get("message", ""))),
                }
                normalized.append(msg)
            elif isinstance(item, str):
                normalized.append({"role": "unknown", "content": item})
        return normalized


class CodexSessionParser:
    """Parse Codex CLI session files.

    Codex stores sessions in ~/.codex/sessions/YYYY/MM/DD/ as JSONL files.
    Each line is a RolloutItem. Codex uses nested message structures;
    this parser recursively searches for message-like objects.
    """

    DEFAULT_SESSION_DIR = Path.home() / ".codex" / "sessions"

    def __init__(self, session_dir: Path | str | None = None) -> None:
        self._dir = Path(session_dir) if session_dir else self.DEFAULT_SESSION_DIR

    def find_latest_session(self) -> Path | None:
        """Find the most recently modified JSONL session file."""
        if not self._dir.exists():
            return None

        jsonl_files = list(self._dir.rglob("*.jsonl"))
        if not jsonl_files:
            return None

        return max(jsonl_files, key=lambda f: f.stat().st_mtime)

    def parse(self, session_path: Path | str | None = None) -> SessionSnapshot:
        """Parse a Codex CLI JSONL session file."""
        path = Path(session_path) if session_path else self.find_latest_session()
        if path is None or not path.exists():
            return _empty_snapshot()

        messages: list[dict[str, Any]] = []
        state_vars: dict[str, Any] = {}

        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if isinstance(item, dict):
                    # Try multiple Codex JSONL shapes
                    self._extract_messages(item, messages)
                    if "state" in item and isinstance(item["state"], dict):
                        state_vars.update(item["state"])

        snapshot = _build_snapshot(messages, path)
        snapshot.state_variables = state_vars
        return snapshot

    def _extract_messages(
        self, item: dict[str, Any], out: list[dict[str, Any]]
    ) -> None:
        """Recursively extract message-like objects from a Codex item."""
        # Direct message fields
        msg = self._try_extract_message(item)
        if msg:
            out.append(msg)
            return

        # Nested structures: look for message arrays or nested dicts
        for key, value in item.items():
            if isinstance(value, list):
                for child in value:
                    if isinstance(child, dict):
                        self._extract_messages(child, out)
            elif isinstance(value, dict):
                child_msg = self._try_extract_message(value)
                if child_msg:
                    out.append(child_msg)

    def _try_extract_message(self, obj: dict[str, Any]) -> dict[str, Any] | None:
        """Try to extract a normalized message from a dict."""
        content = obj.get("content") or obj.get("message") or obj.get("text")
        if not content:
            return None

        role = obj.get("role") or obj.get("actor") or obj.get("type", "unknown")
        # Codex sometimes uses 'output' for assistant responses
        if role in ("output", "response", "completion"):
            role = "assistant"
        elif role in ("input", "prompt"):
            role = "user"

        return {"role": role, "content": str(content)}


class OpenCodeSessionParser:
    """Parse OpenCode session files (experimental).

    OpenCode stores sessions in ~/.local/share/opencode/sessions/ as JSON.
    Format is MessageV2 structure; support is best-effort.
    """

    DEFAULT_SESSION_DIR = Path.home() / ".local" / "share" / "opencode" / "sessions"

    def __init__(self, session_dir: Path | str | None = None) -> None:
        self._dir = Path(session_dir) if session_dir else self.DEFAULT_SESSION_DIR

    def find_latest_session(self) -> Path | None:
        """Find the most recently modified session file."""
        if not self._dir.exists():
            return None

        json_files = [
            f for f in self._dir.iterdir()
            if f.is_file() and f.suffix == ".json"
        ]
        if not json_files:
            return None

        return max(json_files, key=lambda f: f.stat().st_mtime)

    def parse(self, session_path: Path | str | None = None) -> SessionSnapshot:
        """Parse an OpenCode session file."""
        path = Path(session_path) if session_path else self.find_latest_session()
        if path is None or not path.exists():
            return _empty_snapshot()

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        messages: list[dict[str, Any]] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    msg = {
                        "role": item.get("role", "unknown"),
                        "content": item.get("content", item.get("text", "")),
                    }
                    messages.append(msg)
        elif isinstance(data, dict):
            for key in ("messages", "conversation", "history"):
                if key in data and isinstance(data[key], list):
                    for item in data[key]:
                        if isinstance(item, dict):
                            messages.append({
                                "role": item.get("role", "unknown"),
                                "content": item.get("content", item.get("text", "")),
                            })
                    break

        return _build_snapshot(messages, path)


def get_parser(agent_type: str) -> ClaudeCodeSessionParser | CodexSessionParser | OpenCodeSessionParser:
    """Get the appropriate session parser for an agent type.

    Args:
        agent_type: "claude-code", "codex-cli", or "opencode"

    Returns:
        Parser instance.

    Raises:
        ValueError: If agent_type is not supported.
    """
    if agent_type == "claude-code":
        return ClaudeCodeSessionParser()
    elif agent_type == "codex-cli":
        return CodexSessionParser()
    elif agent_type == "opencode":
        return OpenCodeSessionParser()
    raise ValueError(
        f"Unsupported agent type: {agent_type!r}. "
        f"Supported: claude-code, codex-cli, opencode"
    )


def _empty_snapshot() -> SessionSnapshot:
    """Return an empty session snapshot."""
    return SessionSnapshot(
        messages=[],
        current_task="",
        last_user_message="",
        last_assistant_message="",
        state_variables={},
        timestamp=None,
        session_id=None,
    )


def _build_snapshot(
    messages: list[dict[str, Any]], path: Path
) -> SessionSnapshot:
    """Build a SessionSnapshot from normalized messages and a file path."""
    last_user = ""
    last_assistant = ""
    for msg in reversed(messages):
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user" and not last_user:
            last_user = content[:500]
        elif role in ("assistant", "model", "agent") and not last_assistant:
            last_assistant = content[:500]
        if last_user and last_assistant:
            break

    current_task = ""
    if messages:
        first_user = next(
            (m.get("content", "") for m in messages if m.get("role") == "user"),
            "",
        )
        current_task = first_user[:200]

    timestamp = datetime.fromtimestamp(path.stat().st_mtime)

    return SessionSnapshot(
        messages=messages[-50:],
        current_task=current_task,
        last_user_message=last_user,
        last_assistant_message=last_assistant,
        state_variables={},
        timestamp=timestamp,
        session_id=path.stem,
    )
