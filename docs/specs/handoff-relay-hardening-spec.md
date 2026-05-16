# Handoff Relay Hardening Specification

> Target implementer: Kimi or another coding agent.
> Repository: `agent-context-handoff`
> Date: 2026-05-17

## Goal

Bring the local CLI agent handoff relay from a partially working prototype to a coherent, testable integration surface for Claude Code, Codex CLI, and generic/OpenCode-style agents.

The immediate objective is not to redesign the whole library. Fix correctness gaps, dependency/documentation drift, unsafe project-file mutation, and broken relay commands while preserving the existing package model and orchestrator architecture.

## Current Evidence

- Core tests currently pass with warnings: `108 passed, 6 warnings` using `.venv/bin/pytest tests/ -q`.
- Lint passes: `.venv/bin/ruff check .`.
- Type checking passes: `.venv/bin/mypy src`.
- README-recommended strict warning test currently fails: `.venv/bin/pytest tests/ -W error::DeprecationWarning -q`.
- Main issue cluster is in `src/handoff_relay/*`, dependency extras, README claims, and backend edge cases.

## Non-Goals

- Do not replace the core `ContextPackage` schema.
- Do not add heavyweight orchestration or new runtime frameworks.
- Do not introduce new dependencies unless they are already implied by existing code and documented as optional extras.
- Do not rewrite the FastAPI service unless needed to fix documented behavior.

## Severity-Prioritized Requirements

### R1: Fix invalid handoff reason defaults

Problem:
- `src/handoff_relay/cli.py` uses `reason="manual"` in `create`.
- `src/handoff_relay/mcp_server.py` uses `reason="manual"` in `handoff_create_package`.
- `handoff.models.task.HandoffReason` does not define `manual`; default calls fail when constructing `HandoffReason(reason)`.

Required behavior:
- Default relay reason must map to a valid enum value, preferably `user_triggered`.
- CLI help text must list only valid enum values.
- If a user passes legacy values `manual`, `rate_limit`, or `error`, the CLI/MCP layer should either:
  - map them to valid enum values (`manual -> user_triggered`, `error -> error_recovery`), or
  - reject with a clear user-facing error.
- Prefer compatibility mapping for `manual` because current generated docs mention it.

Files:
- `src/handoff_relay/cli.py`
- `src/handoff_relay/mcp_server.py`
- Add/modify tests under `tests/`.

Acceptance tests:
- `handoff-relay create --source codex-cli --task t1` no longer fails because of the default reason.
- MCP `handoff_create_package(source_agent="codex-cli", task_id="t1")` no longer fails because of the default reason.
- A test explicitly covers `manual -> user_triggered`.

### R2: Implement or remove the broken Claude Code hook command

Problem:
- `ClaudeCodeAdapter.generate_hooks_config()` writes `handoff-relay hook session-stop`.
- `handoff-relay` has no `hook` subcommand.
- `handoff-relay init --agent claude-code` therefore installs a nonfunctional hook.

Required behavior:
- Add a `hook` command group with at least `session-stop`.
- `session-stop` should create a handoff package from the latest Claude Code session using a valid default reason.
- It should not require interactive input.
- It should return concise JSON or a stable text line containing the package id.
- If no Claude Code session exists, it should exit cleanly with a clear message and nonzero exit code only if appropriate for Claude hook behavior.

Files:
- `src/handoff_relay/cli.py`
- `src/handoff_relay/adapters/claude_code.py`
- Tests under `tests/`.

Acceptance tests:
- CLI exposes `handoff-relay hook session-stop`.
- Generated `.claude/settings.local.json` points to an implemented command.
- The hook command can be tested using a temporary fake Claude session directory or injected parser path if needed.

### R3: Stop writing temporary handoff context through a `CLAUDE.md -> AGENTS.md` symlink

Problem:
- `handoff-relay init` creates `CLAUDE.md` as a symlink to `AGENTS.md` on non-Windows.
- `ClaudeCodeAdapter.inject_into_claude_md()` writes a temporary handoff block to `CLAUDE.md`.
- If `CLAUDE.md` is a symlink, this mutates `AGENTS.md`, which is the long-lived instruction file.

Required behavior:
- Temporary handoff injection must not mutate `AGENTS.md`.
- Options:
  - Preferred: if `CLAUDE.md` is a symlink, replace it with a real file that includes stable project instructions plus the handoff block.
  - Alternative: avoid symlinking during init and create a real `CLAUDE.md` from the start.
- Existing `AGENTS.md` must be preserved exactly during injection and cleanup.
- `cleanup_claude_md()` must remove only the handoff block and leave stable content intact.

Files:
- `src/handoff_relay/cli.py`
- `src/handoff_relay/adapters/claude_code.py`
- Tests under `tests/`.

Acceptance tests:
- Create temp project with `AGENTS.md` and symlinked `CLAUDE.md`.
- Run injection.
- Assert `AGENTS.md` content is unchanged.
- Assert `CLAUDE.md` contains the handoff block as a real file or otherwise does not mutate the symlink target.

### R4: Add missing optional dependency extras and align install docs

Problem:
- Code refers to extras `[mcp]`, `[a2a]`, and S3 dependencies, but `pyproject.toml` defines only `redis`, `postgres`, `crypto`, `api`, `cli`, and `dev`.
- README says “all backends and features” but the install command does not include all relevant extras.

Required behavior:
- Add optional extras:
  - `mcp = ["mcp>=..."]`
  - `a2a = ["a2a-sdk>=..."]` if package name is correct; verify package naming before pinning.
  - `s3 = ["aiobotocore>=..."]` or `boto3>=...`, matching actual async client expectation.
  - `all = [...]` aggregating all optional runtime extras.
- Keep `dev` for dev tooling and tests.
- README install instructions must match `pyproject.toml`.
- Error messages in code must point to valid extras.

Files:
- `pyproject.toml`
- `README.md`
- Possible code strings in:
  - `src/handoff/mcp_adapter/server.py`
  - `src/handoff_relay/mcp_server.py`
  - `src/handoff/a2a_adapter/mapper.py`
  - `src/handoff/orchestrator/s3_store.py`

Acceptance tests:
- `pip install -e ".[cli,mcp]"` should be a valid extras expression.
- README no longer advertises nonexistent extras.

### R5: Enforce expiration in `LocalHandoffStore.load`

Problem:
- Core `HandoffStore.load()` contract says expired packages should not be returned.
- `InMemoryHandoffStore.load()` enforces expiry.
- `LocalHandoffStore.load()` reads the JSON file directly and returns expired packages.

Required behavior:
- `LocalHandoffStore.load(package_id)` must return `None` for expired packages.
- It should optionally delete expired package files and index rows on access, matching in-memory auto-cleanup behavior.
- `show`, `inject`, and MCP retrieval should not expose expired packages.

Files:
- `src/handoff_relay/storage/local_store.py`
- Tests under `tests/`.

Acceptance tests:
- Save package with past `expires_at`.
- `load(package_id)` returns `None`.
- Package is no longer injectable through CLI/generic injection path.

### R6: Make OpenCode support honest

Problem:
- CLI/MCP help advertises `opencode`.
- `get_parser("opencode")` currently falls back to `ClaudeCodeSessionParser`, which is misleading.

Required behavior:
- Either implement a real `OpenCodeSessionParser` or stop advertising OpenCode as supported.
- If OpenCode support remains partial, label it as generic/manual only.
- Unknown agent types must not silently use Claude parser.
- `get_parser()` should raise a clear `ValueError` for unsupported agent types.

Files:
- `src/handoff_relay/adapters/session_parser.py`
- `src/handoff_relay/cli.py`
- `src/handoff_relay/mcp_server.py`
- `README.md`
- Tests under `tests/`.

Acceptance tests:
- `get_parser("unknown")` raises a clear error.
- `get_parser("opencode")` either returns a real OpenCode parser or raises an unsupported-agent error with guidance.

### R7: Improve Codex session parsing enough to be useful

Problem:
- `CodexSessionParser` only extracts top-level `message` or `content`.
- Codex session JSONL commonly stores rollout items in nested structures; shallow parsing may produce empty summaries.

Required behavior:
- Add robust extraction for known Codex JSONL rollout shapes used by current Codex CLI/App sessions.
- The parser should extract:
  - user messages
  - assistant messages
  - session id from filename
  - latest task from first meaningful user message
- Invalid JSONL lines should still be skipped.

Files:
- `src/handoff_relay/adapters/session_parser.py`
- Tests under `tests/`.

Acceptance tests:
- Unit test with representative nested Codex JSONL examples.
- Unit test for malformed lines.
- Unit test for empty session directory.

### R8: Make `handoff_capture_state` actually persist or rename it

Problem:
- MCP tool `handoff_capture_state` returns `capture_id` and estimated token count but stores nothing.
- This creates a false sense that state has been captured.

Required behavior:
- Preferred: persist captured state as a `ContextPackage` in `LocalHandoffStore`.
- Return `package_id`, `file_path`, `estimated_token_count`, and `status`.
- If this is intentionally only a dry-run estimator, rename it and update docs.

Files:
- `src/handoff_relay/mcp_server.py`
- Tests under `tests/`.

Acceptance tests:
- Calling `handoff_capture_state` produces a retrievable package.
- `handoff_get_package(package_id)` returns stored content.

### R9: Fix strict warning test failure

Problem:
- README recommends `pytest tests/ -W error::DeprecationWarning`.
- Current tests fail because `tests/test_stores.py` uses deprecated `await client.close()`.

Required behavior:
- Replace deprecated Redis/fakeredis close call with `await client.aclose()`.
- Strict warning test should pass.

Files:
- `tests/test_stores.py`
- `README.md` only if command changes, but prefer keeping command and making it pass.

Acceptance tests:
- `.venv/bin/pytest tests/ -W error::DeprecationWarning -q` passes.

## Backend Quality Improvements

### R10: Harden PostgreSQL store payload and table handling

Problems:
- `PostgresHandoffStore` interpolates `self._table` into SQL.
- `load()` converts dict payloads with `str(payload)`, which is not JSON.
- `ensure_schema()` always creates the hard-coded `handoff_packages` table and ignores custom `table_name`.

Required behavior:
- Validate `table_name` against a strict identifier regex before storing it.
- If custom table names are supported, schema DDL must use the validated table name.
- On load, handle `payload_json` as:
  - string JSON: pass through
  - dict/list: `json.dumps(payload)`
- Add tests with fake asyncpg-like pool/connection if no live Postgres is available.

Files:
- `src/handoff/orchestrator/postgres_store.py`
- Tests under `tests/`.

Acceptance tests:
- Dict payload round-trips.
- Invalid table name is rejected.
- Custom valid table name is used consistently.

### R11: Clarify and test S3 async client contract

Problems:
- `S3HandoffStore` claims boto3 or aiobotocore support but methods are async and use `await`.
- `load()` assumes `response["Body"]` is an async context manager.
- README labels S3 Done while also saying untested.

Required behavior:
- Pick one supported client style:
  - async aiobotocore-only, or
  - sync boto3 wrapped separately.
- Document the expected client interface.
- Make `load()` compatible with that interface.
- If S3 remains untested, do not claim production-grade support.

Files:
- `src/handoff/orchestrator/s3_store.py`
- `pyproject.toml`
- `README.md`
- Tests under `tests/`.

Acceptance tests:
- Fake async S3 client can save, load, delete.
- No misleading boto3 support claim unless sync support is implemented.

## Documentation Requirements

Update `README.md` so it clearly distinguishes:

- Core library: `handoff` models, orchestrator, stores, injector.
- Local relay: `handoff-relay` CLI/MCP for Claude Code/Codex/OpenCode-style handoff.
- Supported integrations by status:
  - Claude Code: supported after hook/injection fixes.
  - Codex CLI: supported if parser improvements land.
  - OpenCode: supported only if real parser/injection exists; otherwise mark as not yet implemented/manual generic.
  - MCP: supported with valid `[mcp]` extra.
  - A2A: supported only to the extent the adapter is tested against expected dict shapes; do not overclaim live SDK validation unless added.

README must include:
- Correct installation commands.
- Correct CLI examples.
- Correct MCP server setup command.
- Storage location for local relay packages: `~/.handoff/`.
- Expiration behavior.
- Test commands that actually pass.

## Suggested Implementation Order

1. R1, R9: fastest correctness and verification fixes.
2. R4: dependency extras and install-doc correctness.
3. R2, R3: make Claude Code integration safe and functional.
4. R5: fix local store expiry.
5. R6, R7: make Codex/OpenCode claims match implementation.
6. R8: make relay MCP capture real.
7. R10, R11: backend hardening.
8. README update and final verification.

## Required Final Verification

Run these before declaring done:

```bash
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/pytest tests/ -q
.venv/bin/pytest tests/ -W error::DeprecationWarning -q
```

If changing packaging extras, also run at least:

```bash
python -m pip install -e ".[cli]"
python -m pip install -e ".[cli,mcp]"
```

If adding `all` extra:

```bash
python -m pip install -e ".[all]"
```

## Definition of Done

- No advertised default command fails due to invalid enum values.
- `handoff-relay init --agent claude-code` does not install dead commands.
- Handoff injection never mutates `AGENTS.md` through a `CLAUDE.md` symlink.
- Optional extras referenced by code/docs exist in `pyproject.toml`.
- Expired local handoff packages are not returned or injected.
- OpenCode and Codex support claims are accurate.
- Strict warning test passes.
- README examples match working behavior.

