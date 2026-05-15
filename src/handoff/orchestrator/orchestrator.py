"""Handoff Orchestrator — central controller for context handoff."""

from __future__ import annotations

import logging
from datetime import timedelta

from handoff._utils import utc_now
from enum import Enum
from typing import Any

from handoff.models.package import ContextPackage
from handoff.models.task import HandoffReason
from handoff.orchestrator.injector import ContextInjector, InjectionError, PromptBasedInjector
from handoff.orchestrator.selector import AgentDescriptor, AgentSelector, CapabilityBasedSelector
from handoff.orchestrator.store import HandoffStore, InMemoryHandoffStore, StoreError

logger = logging.getLogger(__name__)


class HandoffStatus(str, Enum):
    """Status of a handoff operation."""

    PENDING = "pending"
    PACKED = "packed"
    STORED = "stored"
    DELIVERED = "delivered"
    RESUMED = "resumed"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class HandoffResult:
    """Result of a handoff operation."""

    def __init__(
        self,
        package_id: str,
        status: HandoffStatus,
        target_agent_id: str | None = None,
        session_id: str | None = None,
        errors: list[str] | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        self.package_id = package_id
        self.status = status
        self.target_agent_id = target_agent_id
        self.session_id = session_id
        self.errors = errors or []
        self.warnings = warnings or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "status": self.status.value,
            "target_agent_id": self.target_agent_id,
            "session_id": self.session_id,
            "errors": self.errors,
            "warnings": self.warnings,
        }


class HandoffOrchestrator:
    """Central orchestrator for agent context handoff.

    The orchestrator is the **only** entry point for initiating handoffs.
    It coordinates: progress summarization, packaging, storage, target
    selection, and context injection.
    """

    def __init__(
        self,
        store: HandoffStore | None = None,
        selector: AgentSelector | None = None,
        injector: ContextInjector | None = None,
        default_ttl_seconds: int = 3600,
    ) -> None:
        self.store = store or InMemoryHandoffStore()
        self.selector = selector or CapabilityBasedSelector()
        self.injector = injector or PromptBasedInjector()
        self.default_ttl_seconds = default_ttl_seconds
        self._audit_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def initiate(
        self,
        source_agent_id: str,
        reason: HandoffReason,
        package: ContextPackage,
        target_constraints: dict[str, Any] | None = None,
        candidates: list[AgentDescriptor] | None = None,
    ) -> HandoffResult:
        """Initiate a full handoff from source to target agent.

        This is the main entry point. It performs the complete handoff
        pipeline: sanitize, store, select, inject.

        Args:
            source_agent_id: ID of the agent handing off work.
            reason: Why the handoff is occurring.
            package: The prepared context package (progress summary must
                already be populated).
            target_constraints: Optional constraints for target selection.
            candidates: Pool of available target agents. If None, the
                orchestrator cannot auto-select and will return PENDING.

        Returns:
            HandoffResult describing the outcome.
        """
        package_id = package.meta.package_id
        trace_id = package.meta.trace_id

        logger.info(
            "[trace=%s] Initiating handoff: %s -> reason=%s",
            trace_id,
            source_agent_id,
            reason.value,
        )

        try:
            # 1. Sanitize and validate
            package = self._sanitize_package(package)
            security_errors = package.validate_security()
            if any("security:" in e for e in security_errors):
                return self._fail(package_id, security_errors)

            # 2. Ensure TTL
            if package.meta.expires_at is None:
                package.meta.expires_at = utc_now() + timedelta(
                    seconds=self.default_ttl_seconds
                )

            # 3. Store
            await self.store.save(package)
            self._audit(
                trace_id=trace_id,
                event="stored",
                package_id=package_id,
                source=source_agent_id,
            )

            # 4. Select target (if candidates provided)
            if not candidates:
                logger.info("[trace=%s] No candidates provided; handoff pending", trace_id)
                return HandoffResult(
                    package_id=package_id,
                    status=HandoffStatus.PENDING,
                    warnings=["No target candidates provided; package stored awaiting delivery"],
                )

            target = await self.selector.select(
                candidates=candidates,
                required_capabilities=package.task.required_capabilities,
                priority=package.meta.priority.value,
            )
            if target is None:
                return HandoffResult(
                    package_id=package_id,
                    status=HandoffStatus.PENDING,
                    warnings=["No suitable target agent found; package stored awaiting delivery"],
                )

            # 5. Inject into target
            injection_meta = await self.injector.inject(
                target_agent_id=target.agent_id,
                package=package,
            )

            self._audit(
                trace_id=trace_id,
                event="injected",
                package_id=package_id,
                source=source_agent_id,
                target=target.agent_id,
                session_id=injection_meta.get("session_id"),
            )

            return HandoffResult(
                package_id=package_id,
                status=HandoffStatus.RESUMED,
                target_agent_id=target.agent_id,
                session_id=injection_meta.get("session_id"),
                warnings=injection_meta.get("warnings", []),
            )

        except StoreError as exc:
            logger.error("[trace=%s] Store error: %s", trace_id, exc)
            return self._fail(package_id, [f"Store error: {exc}"])
        except InjectionError as exc:
            logger.error("[trace=%s] Injection error: %s", trace_id, exc)
            return self._fail(package_id, [f"Injection error: {exc}"])
        except Exception as exc:
            logger.exception("[trace=%s] Unexpected handoff error", trace_id)
            return self._fail(package_id, [f"Unexpected error: {exc}"])

    async def get_status(self, package_id: str) -> dict[str, Any]:
        """Get the current status of a handoff package.

        Args:
            package_id: The package to query.

        Returns:
            Status dictionary.
        """
        package = await self.store.load(package_id)
        if package is None:
            return {"package_id": package_id, "status": "unknown"}

        return {
            "package_id": package_id,
            "status": "stored",
            "source_agent": package.meta.source.agent_id,
            "handoff_reason": package.meta.handoff_reason.value,
            "expires_at": package.meta.expires_at.isoformat() if package.meta.expires_at else None,
            "expired": package.is_expired(),
        }

    async def cleanup_expired(self) -> int:
        """Remove all expired packages from storage.

        Returns:
            Number of packages removed.
        """
        expired_ids = await self.store.list_expired()
        for pid in expired_ids:
            await self.store.delete(pid)
            logger.info("Cleaned up expired package: %s", pid)
        return len(expired_ids)

    def get_audit_log(self) -> list[dict[str, Any]]:
        """Return the audit log for compliance review.

        Returns:
            Copy of the audit log entries.
        """
        return list(self._audit_log)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sanitize_package(self, package: ContextPackage) -> ContextPackage:
        """Apply security sanitization to a package.

        If no whitelist is set, auto-whitelist all current keys as a safe
        default for Phase 1. If a whitelist IS explicitly set, validate
        strictly and fail if violations exist rather than silently dropping
        data.

        Returns a new sanitized package (immutable operation).
        """
        # If no whitelist is set, use a safe default for Phase 1
        if not package.security.allowed_variable_keys:
            package = package.model_copy(deep=True)
            package.security.allowed_variable_keys = list(
                package.context.state.variables.keys()
            )
            return package.sanitize()
        # Explicit whitelist: sanitize (removes disallowed keys)
        return package.sanitize()

    def _fail(self, package_id: str, errors: list[str]) -> HandoffResult:
        return HandoffResult(
            package_id=package_id,
            status=HandoffStatus.FAILED,
            errors=errors,
        )

    def _audit(self, **kwargs: Any) -> None:
        entry = {"timestamp": utc_now().isoformat(), **kwargs}
        self._audit_log.append(entry)
