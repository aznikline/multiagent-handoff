"""FastAPI HTTP server for agent-context-handoff.

Provides REST endpoints for initiating, querying, and managing
context handoffs. Designed for deployment on Vercel (serverless)
or as a standalone ASGI service.

Environment Variables:
    REDIS_URL: Optional Redis connection URL. If set, Redis store is used.
    HANDOFF_TTL_SECONDS: Default TTL for stored packages (default: 3600).
"""

from __future__ import annotations

import os
import sys
from typing import Any

from pydantic import BaseModel, Field

# Ensure src/ is on path for imports when running from api/index.py
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
_src_dir = os.path.join(_project_root, "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from handoff.models.package import ContextPackage, PackageMeta, SourceInfo  # noqa: E402
from handoff.models.task import HandoffReason, ProgressSummary, TaskInfo  # noqa: E402
from handoff.orchestrator.orchestrator import HandoffOrchestrator  # noqa: E402
from handoff.orchestrator.selector import AgentDescriptor  # noqa: E402
from handoff.orchestrator.store import InMemoryHandoffStore  # noqa: E402


# ------------------------------------------------------------------
# Pydantic request/response models
# ------------------------------------------------------------------

class CandidateAgent(BaseModel):
    """Simplified agent descriptor for API requests."""

    agent_id: str
    capabilities: list[str] = Field(default_factory=list)
    current_load: int = 0
    max_concurrency: int = 1
    token_window_remaining: int = 0
    framework: str = "custom"
    version: str = "1.0.0"
    accepts_handoff: bool = True


class InitiateHandoffRequest(BaseModel):
    """Request body for POST /handoff/initiate."""

    source_agent_id: str = Field(description="ID of the agent initiating the handoff.")
    task_id: str = Field(description="Stable identifier for the logical task.")
    task_description: str = Field(description="Description of the original task.")
    reason: str = Field(
        default="task_delegation",
        description="Handoff reason: token_limit, task_delegation, error_recovery, user_triggered, capability_mismatch, scheduled.",
    )
    progress_summary: str = Field(
        default="",
        description="Human-readable summary of current progress.",
    )
    required_capabilities: list[str] = Field(default_factory=list)
    candidates: list[CandidateAgent] = Field(
        default_factory=list,
        description="Pool of available target agents. If empty, handoff is stored but not routed.",
    )


class HandoffResponse(BaseModel):
    """Standard handoff response envelope."""

    success: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


# ------------------------------------------------------------------
# Orchestrator singleton (module-level, safe for serverless)
# ------------------------------------------------------------------

_orchestrator: HandoffOrchestrator | None = None


def _create_store() -> InMemoryHandoffStore | Any:
    """Create storage backend based on environment."""
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            import redis.asyncio as redis
            from handoff.orchestrator.redis_store import RedisHandoffStore

            client = redis.from_url(redis_url)
            ttl = int(os.getenv("HANDOFF_TTL_SECONDS", "3600"))
            return RedisHandoffStore(redis_client=client, ttl_seconds=ttl)
        except ImportError:
            pass
    return InMemoryHandoffStore()


def _get_orchestrator() -> HandoffOrchestrator:
    """Lazy-initialize orchestrator (safe for serverless lifespan=off)."""
    global _orchestrator
    if _orchestrator is None:
        store = _create_store()
        ttl = int(os.getenv("HANDOFF_TTL_SECONDS", "3600"))
        _orchestrator = HandoffOrchestrator(store=store, default_ttl_seconds=ttl)
    return _orchestrator


# ------------------------------------------------------------------
# FastAPI app
# ------------------------------------------------------------------


def create_app() -> Any:
    """Factory function to create the FastAPI application."""
    try:
        from fastapi import FastAPI, HTTPException
    except ImportError as exc:
        raise ImportError(
            "HTTP API requires 'fastapi'. Install with: "
            "pip install agent-context-handoff[api]"
        ) from exc

    app = FastAPI(
        title="Agent Context Handoff API",
        description="REST API for agent context packaging and handoff.",
        version="0.1.0",
    )

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok", "service": "agent-context-handoff"}

    @app.post("/handoff/initiate", response_model=HandoffResponse)
    async def initiate_handoff(request: InitiateHandoffRequest) -> HandoffResponse:
        """Initiate a context handoff from source to target agent."""
        orchestrator = _get_orchestrator()

        package = ContextPackage(
            meta=PackageMeta(
                source=SourceInfo(agent_id=request.source_agent_id),
                handoff_reason=HandoffReason(request.reason),
            ),
            task=TaskInfo(
                original_task_id=request.task_id,
                description=request.task_description,
                progress_summary=ProgressSummary(
                    current_step=request.progress_summary,
                ),
                required_capabilities=request.required_capabilities,
            ),
        )

        candidates = [
            AgentDescriptor(
                agent_id=c.agent_id,
                capabilities=frozenset(c.capabilities),
                current_load=c.current_load,
                max_concurrency=c.max_concurrency,
                token_window_remaining=c.token_window_remaining,
                framework=c.framework,
                version=c.version,
                accepts_handoff=c.accepts_handoff,
            )
            for c in request.candidates
        ] or None

        result = await orchestrator.initiate(
            source_agent_id=request.source_agent_id,
            reason=HandoffReason(request.reason),
            package=package,
            candidates=candidates,
        )

        if result.status.value == "failed":
            return HandoffResponse(
                success=False,
                data=result.to_dict(),
                error="; ".join(result.errors),
            )

        return HandoffResponse(success=True, data=result.to_dict())

    @app.get("/handoff/{package_id}")
    async def get_package(package_id: str) -> HandoffResponse:
        """Retrieve a context package by ID."""
        orchestrator = _get_orchestrator()
        package = await orchestrator.store.load(package_id)

        if package is None:
            raise HTTPException(status_code=404, detail="Package not found")

        return HandoffResponse(success=True, data={"package": package.model_dump()})

    @app.get("/handoff/{package_id}/status")
    async def get_status(package_id: str) -> HandoffResponse:
        """Get the current status of a handoff package."""
        orchestrator = _get_orchestrator()
        status = await orchestrator.get_status(package_id)
        return HandoffResponse(success=True, data=status)

    @app.post("/handoff/cleanup")
    async def cleanup_expired() -> HandoffResponse:
        """Remove all expired handoff packages from storage."""
        orchestrator = _get_orchestrator()
        count = await orchestrator.cleanup_expired()
        return HandoffResponse(success=True, data={"cleaned_count": count})

    @app.get("/audit-log")
    async def get_audit_log(limit: int = 100) -> HandoffResponse:
        """Return recent audit log entries."""
        orchestrator = _get_orchestrator()
        log = orchestrator.get_audit_log()
        return HandoffResponse(success=True, data={"entries": log[-limit:]})

    return app
