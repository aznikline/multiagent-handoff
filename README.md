# Agent Context Handoff

Phase 1 + Phase 2 + Phase 3 implementation of the **Agent Context Packaging & Handoff Specification v1.0**.

A standardized, secure, and framework-agnostic system for one agent to hand off its task context to another agent seamlessly. Includes production-hardened storage backends, encryption, schema migrations, automatic triggering, A2A/MCP protocol integration, and cross-framework state adapters.

## Architecture

```
┌─────────────────────────────────────────┐
│      HandoffOrchestrator (管控面)        │
│  - Security sanitization & validation   │
│  - Context package storage (TTL)        │
│  - Target agent selection               │
│  - Prompt-based context injection       │
│  - Audit logging                        │
└──────────────┬──────────────────────────┘
               │
    ┌──────────┴──────────┐
    ▼                     ▼
┌──────────┐       ┌──────────────┐
│  Store   │       │  Summarizer  │
│ (memory/ │       │ (LLM +       │
│  redis/  │       │  fallback)   │
│  s3)     │       └──────────────┘
└──────────┘
```

## Quick Start

### Installation

```bash
# Core only
pip install -e "."

# With all backends and features
pip install -e ".[redis,postgres,crypto,dev]"
```

### Basic Usage

```python
import asyncio
from handoff import HandoffOrchestrator
from handoff.models.package import ContextPackage, PackageMeta, SourceInfo
from handoff.models.task import TaskInfo, HandoffReason, ProgressSummary
from handoff.orchestrator.selector import AgentDescriptor

async def main():
    orchestrator = HandoffOrchestrator()

    # Build a context package
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

    # Define target agent candidates
    candidates = [
        AgentDescriptor(
            agent_id="agent-b",
            capabilities=frozenset({"research", "summarize"}),
            current_load=0,
            max_concurrency=2,
        ),
    ]

    # Initiate handoff
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

## Project Structure

```
src/handoff/
├── models/              # ContextPackage, security, task, context schemas
│   ├── package.py       # Core data structure
│   ├── security.py      # Whitelist sanitization, PII scrubbing
│   ├── task.py          # Task info, progress summary, checkpoint refs
│   └── context.py       # Conversation, state, memory
├── serialization/       # JSON + encrypted serializers
│   ├── serializer.py
│   └── encrypted_serializer.py
├── orchestrator/        # HandoffOrchestrator + stores + selector + injector
│   ├── orchestrator.py
│   ├── store.py         # In-memory store
│   ├── redis_store.py   # Redis backend
│   ├── postgres_store.py# PostgreSQL backend
│   ├── selector.py      # Capability-based agent selection
│   └── injector.py      # Prompt-based context injection
├── summarizer/          # LLM-based + rule-based fallback summarizers
├── monitor.py           # Token-usage monitor with auto-trigger
├── migrations.py        # Schema version migration framework
├── crypto.py            # AES-256-GCM encryption
├── a2a_adapter/         # Google A2A Protocol mapper + client
├── mcp_adapter/         # MCP Server (FastMCP) with tools/resources/prompts
├── framework_adapter/   # LangGraph ↔ CrewAI state converters
└── skills/              # CONTEXT_HANDOFF.md skill template
```

## Key Features

| Feature | Status |
|---------|--------|
| ContextPackage JSON Schema | Done |
| Forward compatibility (ignore unknown fields) | Done |
| Security whitelist + PII scrubbing | Done |
| In-memory store with TTL | Done |
| **Redis store** | Done |
| **PostgreSQL store** | Done |
| Capability-based agent selection | Done |
| Prompt-based context injection | Done |
| LLM progress summarizer + fallback | Done |
| Audit logging | Done |
| **AES-256-GCM encryption at rest** | Done |
| **Schema version migration** | Done |
| **Token monitor with auto-trigger** | Done |
| **A2A Protocol adapter** | Done |
| **MCP Server adapter** | Done |
| **LangGraph ↔ CrewAI state adapter** | Done |
| **S3 store** | Done (untested, requires AWS) |

## Testing

```bash
# Run all tests
pytest tests/ -v

# With coverage (83%)
pytest tests/ --cov=handoff --cov-report=term-missing

# Treat warnings as errors
pytest tests/ -W error::DeprecationWarning
```

## Design Decisions

1. **Orchestrator is the sole entry point** — Agents cannot self-initiate handoffs.
2. **Explicit whitelist over blacklist** — Only allowed variable keys survive sanitization.
3. **Immutable sanitization** — `sanitize()` returns a new package; original is untouched.
4. **Structured semantic versions** — Framework versions use `{major, minor, patch}` objects.
5. **LLM primary + rule fallback** — Summarizer degrades gracefully when LLM is unavailable.
6. **Trace IDs** — Every package carries a `trace_id` for distributed observability.

## Roadmap

- **Phase 1** (Done): Core MVP — ContextPackage, orchestrator, in-memory store, summarizer
- **Phase 2** (Done): Production hardening — Redis/PostgreSQL stores, encryption, schema migrations, token monitor
- **Phase 3** (Done): Ecosystem integration — A2A protocol, MCP SDK, cross-framework adapters (LangGraph/CrewAI), S3 store

## License

MIT
