# Agent Context Handoff

Implementation of the **Agent Context Packaging & Handoff Specification v1.0** with local CLI agent relay support.

A standardized, secure, and framework-agnostic system for one agent to hand off its task context to another agent seamlessly. Includes production-hardened storage backends, encryption, schema migrations, automatic triggering, A2A/MCP protocol integration, cross-framework state adapters, and a dedicated local CLI agent relay (`handoff-relay`) for Claude Code, Codex CLI, and OpenCode.

## Packages

This repo provides two installable packages:

| Package | Module | Purpose |
|---------|--------|---------|
| `handoff` | `src/handoff/` | Core library — orchestrator, stores, models, adapters |
| `handoff-relay` | `src/handoff_relay/` | Local CLI agent integration — session parsing, CLI, MCP server |

## Architecture

```
┌─────────────────────────────────────────────┐
│         handoff-relay (local CLI)           │
│  ┌─────────────┐    ┌─────────────────────┐ │
│  │ CLI (typer) │    │ MCP Server (FastMCP)│ │
│  └──────┬──────┘    └──────────┬──────────┘ │
│         │                      │            │
│  ┌──────┴──────────────────────┴──────┐     │
│  │  Session Parsers + Claude Adapter   │     │
│  │  - Claude Code hooks & injection    │     │
│  │  - Codex CLI JSONL parsing          │     │
│  │  - OpenCode experimental            │     │
│  └──────────────┬──────────────────────┘     │
└─────────────────┼────────────────────────────┘
                  │
┌─────────────────┼────────────────────────────┐
│      HandoffOrchestrator (core library)      │
│  - Security sanitization & validation        │
│  - Context package storage (TTL)             │
│  - Target agent selection                    │
│  - Prompt-based context injection            │
│  - Audit logging                             │
└────────┬────────┴────────────────────────────┘
         │
    ┌────┴──────────┐
    ▼               ▼
┌──────────┐  ┌──────────────┐
│  Store   │  │  Summarizer  │
│ (memory/ │  │ (LLM +       │
│  redis/  │  │  fallback)   │
│  s3/     │  └──────────────┘
│  pg)     │
└──────────┘
```

## Installation

```bash
# Core only (orchestrator + models + in-memory store)
pip install -e "."

# With CLI and MCP server (recommended for local use)
pip install -e ".[cli,mcp,dev]"

# With all backends and features (Redis, Postgres, S3, crypto, A2A, MCP, CLI)
pip install -e ".[all,dev]"

# Individual extras
pip install -e ".[redis]"     # Redis store
pip install -e ".[postgres]"  # PostgreSQL store
pip install -e ".[s3]"        # S3 store (aiobotocore)
pip install -e ".[crypto]"    # AES-256-GCM encryption
pip install -e ".[a2a]"       # Google A2A Protocol adapter
pip install -e ".[api]"       # FastAPI HTTP server
pip install -e ".[mcp]"       # MCP server
pip install -e ".[cli]"       # handoff-relay CLI
```

## Quick Start

### Core Library

```python
import asyncio
from handoff import HandoffOrchestrator
from handoff.models.package import ContextPackage, PackageMeta, SourceInfo
from handoff.models.task import TaskInfo, HandoffReason, ProgressSummary
from handoff.orchestrator.selector import AgentDescriptor

async def main():
    orchestrator = HandoffOrchestrator()

    package = ContextPackage(
        meta=PackageMeta(
            source=SourceInfo(agent_id="agent-a"),
            handoff_reason=HandoffReason.TOKEN_LIMIT,
        ),
        task=TaskInfo(
            original_task_id="task-1",
            description="Research quantum computing",
            progress_summary=ProgressSummary(
                completed_steps=["Searched arXiv"],
                current_step="Reading paper #2",
                next_expected_action="Extract results",
            ),
        ),
    )

    candidates = [
        AgentDescriptor(
            agent_id="agent-b",
            capabilities=frozenset({"research", "summarize"}),
            current_load=0,
            max_concurrency=2,
        ),
    ]

    result = await orchestrator.initiate(
        source_agent_id="agent-a",
        reason=HandoffReason.TOKEN_LIMIT,
        package=package,
        candidates=candidates,
    )

    print(result.status)           # RESUMED
    print(result.target_agent_id)  # agent-b
    print(result.session_id)       # handoff-<uuid>

asyncio.run(main())
```

### Local CLI Agent Relay (`handoff-relay`)

```bash
# Initialize a project for Claude Code
handoff-relay init --agent claude-code

# Create a handoff package from the current Claude Code session
handoff-relay create --source claude-code --task "feature-x" --reason token_limit

# List pending packages
handoff-relay list --status pending

# Inject a package into the target agent's context
handoff-relay inject <package-id> --target claude-code

# Cleanup expired packages
handoff-relay cleanup --older-than 7

# Start MCP server
handoff-relay serve --mcp
```

#### Claude Code Hooks

After `handoff-relay init`, Claude Code will have:

- `.claude/commands/handoff.md` — `/handoff` slash command
- `.claude/settings.local.json` — hook configuration for `session-stop`, `session-start`
- Safe `CLAUDE.md` handling — if `CLAUDE.md` is a symlink to `AGENTS.md`, handoff context is injected into `.claude/CLAUDE.md` instead to avoid polluting the shared file

#### MCP Tools

When running `handoff-relay serve --mcp`, the following tools are exposed:

| Tool | Description |
|------|-------------|
| `handoff_create_package` | Create a handoff package from current session |
| `handoff_get_package` | Retrieve a package by ID |
| `handoff_list_packages` | List packages with filtering |
| `handoff_capture_state` | Capture and persist agent state |
| `handoff_get_injectable_context` | Generate injectable context for a target agent |

## Project Structure

```
src/
├── handoff/                          # Core library
│   ├── models/                       # ContextPackage, security, task, context schemas
│   │   ├── package.py
│   │   ├── security.py
│   │   ├── task.py
│   │   └── context.py
│   ├── serialization/                # JSON + encrypted serializers
│   │   ├── serializer.py
│   │   └── encrypted_serializer.py
│   ├── orchestrator/                 # HandoffOrchestrator + stores + selector + injector
│   │   ├── orchestrator.py
│   │   ├── store.py                  # In-memory store
│   │   ├── redis_store.py            # Redis backend
│   │   ├── postgres_store.py         # PostgreSQL backend (hardened, SQL injection safe)
│   │   ├── s3_store.py               # S3 backend (aiobotocore)
│   │   ├── selector.py
│   │   └── injector.py
│   ├── summarizer/                   # LLM-based + rule-based fallback summarizers
│   ├── monitor.py                    # Token-usage monitor with auto-trigger
│   ├── migrations.py                 # Schema version migration framework
│   ├── crypto.py                     # AES-256-GCM encryption
│   ├── api/                          # FastAPI HTTP server (Vercel-ready)
│   │   └── server.py
│   ├── a2a_adapter/                  # Google A2A Protocol mapper + client
│   ├── mcp_adapter/                  # MCP Server (FastMCP)
│   └── framework_adapter/            # LangGraph <-> CrewAI state converters
│
└── handoff_relay/                    # Local CLI agent relay
    ├── cli.py                        # Typer CLI entry point
    ├── mcp_server.py                 # MCP server with tool definitions
    ├── adapters/
    │   ├── claude_code.py            # Claude Code adapter (hooks, injection, symlink safety)
    │   └── session_parser.py         # Session parsers for claude-code / codex-cli / opencode
    └── storage/
        └── local_store.py            # SQLite + JSON filesystem store (~/.handoff/)
```

## Feature Matrix

### Core Library (`handoff`)

| Feature | Status |
|---------|--------|
| ContextPackage JSON Schema | Done |
| Forward compatibility (ignore unknown fields) | Done |
| Security whitelist + PII scrubbing | Done |
| In-memory store with TTL | Done |
| Redis store | Done |
| PostgreSQL store (hardened) | Done |
| S3 store | Done (aiobotocore only; no integration tests against real S3) |
| Capability-based agent selection | Done |
| Prompt-based context injection | Done |
| LLM progress summarizer + fallback | Done |
| Audit logging | Done |
| AES-256-GCM encryption at rest | Done |
| Schema version migration | Done |
| Token monitor with auto-trigger | Done |
| A2A Protocol adapter | Done |
| MCP Server adapter | Done |
| LangGraph <-> CrewAI state adapter | Done |
| FastAPI HTTP API (Vercel-ready) | Done |

### Local Relay (`handoff-relay`)

| Feature | Status |
|---------|--------|
| `handoff-relay` CLI (init, create, list, show, inject, cleanup, hook, serve) | Done |
| Claude Code session parser | Done |
| Codex CLI session parser (recursive JSONL) | Done |
| OpenCode session parser | Done (experimental) |
| Claude Code hook generation (`session-stop`, `session-start`) | Done |
| Claude Code `/handoff` slash command | Done |
| `CLAUDE.md` symlink safety | Done |
| `handoff_capture_state` persistence | Done |
| MCP server with 5 tools | Done |
| `LocalHandoffStore` (SQLite + JSON, `~/.handoff/`) | Done |
| Expiry enforcement on load | Done |
| Legacy reason alias normalization (`manual` -> `user_triggered`, `error` -> `error_recovery`) | Done |

### Local Agent Support

| Agent | Session Parsing | Hooks / Injection | MCP Tools | Notes |
|-------|-----------------|-------------------|-----------|-------|
| Claude Code | Yes | Yes (full) | Yes | Richest support — hooks, commands, symlink-safe injection |
| Codex CLI | Yes (recursive JSONL) | No | Yes | Session parsing only; generic brief generation |
| OpenCode | Yes (experimental) | No | Yes | Best-effort JSON parsing |

## Storage & Expiration

Local relay packages are stored in `~/.handoff/`:

- **SQLite index** (`handoff.db`) — metadata, expiry timestamps, audit log
- **JSON files** (`packages/*.json`) — full serialized `ContextPackage`

Expiration behavior:
- `load()` checks expiry before returning — expired packages are deleted from both DB and filesystem
- `cleanup_expired()` removes all expired packages in bulk
- Default TTL: set at package creation via `expires_at`
- Audit events: `created`, `loaded`, `injected`, `expired_access`, `cleaned`

## Testing

```bash
# Run all tests (108 passing)
pytest tests/ -q

# Verbose with coverage
pytest tests/ -v --cov=handoff --cov-report=term-missing

# Strict mode — warnings treated as errors (clean)
pytest tests/ -W error::DeprecationWarning -q

# Type checking (clean)
mypy src

# Linting (clean)
ruff check .
```

## Design Decisions

1. **Orchestrator is the sole entry point** — Agents cannot self-initiate handoffs.
2. **Explicit whitelist over blacklist** — Only allowed variable keys survive sanitization.
3. **Immutable sanitization** — `sanitize()` returns a new package; original is untouched.
4. **Structured semantic versions** — Framework versions use `{major, minor, patch}` objects.
5. **LLM primary + rule fallback** — Summarizer degrades gracefully when LLM is unavailable.
6. **Trace IDs** — Every package carries a `trace_id` for distributed observability.
7. **Symlink-aware injection** — Detects `CLAUDE.md -> AGENTS.md` symlinks and writes to a separate Claude-specific file to avoid polluting shared project documentation.
8. **PostgreSQL table name validation** — Regex `^[a-zA-Z_][a-zA-Z0-9_]*$` prevents SQL injection via custom table names.
9. **S3 aiobotocore-only** — Async S3 operations require aiobotocore; sync boto3 is explicitly unsupported.

## Roadmap

- **Phase 1** (Done): Core MVP — ContextPackage, orchestrator, in-memory store, summarizer
- **Phase 2** (Done): Production hardening — Redis/PostgreSQL/S3 stores, encryption, schema migrations, token monitor
- **Phase 3** (Done): Ecosystem integration — A2A protocol, MCP SDK, cross-framework adapters, FastAPI, Vercel
- **Phase 4** (Done): Local CLI relay — `handoff-relay` CLI, session parsers, Claude Code hooks/injection, MCP server

## License

MIT
