"""PostgreSQL-backed storage for context packages."""

from __future__ import annotations

import json
import re
from typing import Any

from handoff.models.package import ContextPackage
from handoff.orchestrator.store import HandoffStore, StoreError
from handoff.serialization.serializer import JsonSerializer


# Strict SQL identifier: letters, digits, underscores; must start with letter or underscore
_VALID_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_table_name(name: str) -> str:
    """Validate a table name to prevent SQL injection.

    Args:
        name: Proposed table name.

    Returns:
        The validated name.

    Raises:
        ValueError: If the name is not a valid SQL identifier.
    """
    if not _VALID_IDENTIFIER.match(name):
        raise ValueError(
            f"Invalid table name: {name!r}. "
            "Must match ^[a-zA-Z_][a-zA-Z0-9_]*$"
        )
    return name


class PostgresHandoffStore(HandoffStore):
    """Production-grade PostgreSQL store with automatic table management.

    Requires ``asyncpg>=0.29``.

    The table name is validated on init to prevent SQL injection.
    Custom table names are supported but must be valid SQL identifiers.
    """

    def __init__(
        self,
        pool: Any,
        table_name: str = "handoff_packages",
    ) -> None:
        super().__init__()
        self._pool = pool
        self._table = _validate_table_name(table_name)
        self._serializer = JsonSerializer()

    def _ddl(self) -> str:
        """Generate DDL using the validated table name."""
        return f"""
        CREATE TABLE IF NOT EXISTS {self._table} (
            package_id      TEXT PRIMARY KEY,
            trace_id        TEXT NOT NULL,
            spec_version    TEXT NOT NULL,
            source_agent    TEXT NOT NULL,
            handoff_reason  TEXT NOT NULL,
            priority        TEXT NOT NULL,
            payload_json    JSONB NOT NULL,
            expires_at      TIMESTAMPTZ,
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            sanitized       BOOLEAN DEFAULT FALSE,
            classification  TEXT DEFAULT 'internal'
        );
        CREATE INDEX IF NOT EXISTS idx_{self._table}_expires
            ON {self._table}(expires_at)
            WHERE expires_at IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_{self._table}_trace
            ON {self._table}(trace_id);
        """

    async def ensure_schema(self) -> None:
        """Create the table and indexes if they don't exist."""
        async with self._pool.acquire() as conn:
            await conn.execute(self._ddl())

    async def save(self, package: ContextPackage) -> None:
        try:
            payload = self._serializer.serialize(package).decode("utf-8")
            async with self._pool.acquire() as conn:
                await conn.execute(
                    f"""
                    INSERT INTO {self._table}
                        (package_id, trace_id, spec_version, source_agent,
                         handoff_reason, priority, payload_json, expires_at,
                         sanitized, classification)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    ON CONFLICT (package_id) DO UPDATE SET
                        payload_json = EXCLUDED.payload_json,
                        expires_at = EXCLUDED.expires_at,
                        sanitized = EXCLUDED.sanitized,
                        classification = EXCLUDED.classification
                    """,
                    package.meta.package_id,
                    package.meta.trace_id,
                    package.meta.spec_version,
                    package.meta.source.agent_id,
                    package.meta.handoff_reason.value,
                    package.meta.priority.value,
                    payload,
                    package.meta.expires_at,
                    package.security.sanitized,
                    package.security.classification.value,
                )
        except Exception as exc:
            raise StoreError(f"Postgres save failed: {exc}") from exc

    async def load(self, package_id: str) -> ContextPackage | None:
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"""
                    SELECT payload_json FROM {self._table}
                    WHERE package_id = $1
                      AND (expires_at IS NULL OR expires_at > NOW())
                    """,
                    package_id,
                )
                if row is None:
                    return None
                payload = row["payload_json"]
                # asyncpg may return dict/list for JSONB; normalize to JSON string
                if isinstance(payload, (dict, list)):
                    payload = json.dumps(payload)
                if isinstance(payload, str):
                    payload = payload.encode("utf-8")
                return self._serializer.deserialize(payload)
        except Exception as exc:
            raise StoreError(f"Postgres load failed: {exc}") from exc

    async def delete(self, package_id: str) -> bool:
        try:
            async with self._pool.acquire() as conn:
                result = await conn.execute(
                    f"DELETE FROM {self._table} WHERE package_id = $1",
                    package_id,
                )
                # asyncpg returns "DELETE <count>" string
                return "DELETE 1" in result
        except Exception as exc:
            raise StoreError(f"Postgres delete failed: {exc}") from exc

    async def list_expired(self) -> list[str]:
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT package_id FROM {self._table}
                    WHERE expires_at IS NOT NULL AND expires_at <= NOW()
                    """
                )
                return [row["package_id"] for row in rows]
        except Exception as exc:
            raise StoreError(f"Postgres list_expired failed: {exc}") from exc
