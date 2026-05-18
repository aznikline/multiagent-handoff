"""SQLite-backed local storage for handoff packages.

Stores packages in ~/.handoff/packages/ with SQLite indexing and
JSON file storage for the actual package payloads.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from handoff._utils import utc_now
from handoff.models.package import ContextPackage
from handoff.serialization.serializer import JsonSerializer


DEFAULT_HANDOFF_DIR = Path.home() / ".handoff"
DEFAULT_PACKAGES_DIR = DEFAULT_HANDOFF_DIR / "packages"
DEFAULT_DB_PATH = DEFAULT_HANDOFF_DIR / "handoff.db"


class LocalHandoffStore:
    """Local filesystem + SQLite storage for handoff packages.

    File layout:
        ~/.handoff/
        ├── handoff.db          # SQLite index
        └── packages/
            ├── pkg-xxx.json    # Package payload
            └── ...
    """

    def __init__(
        self,
        base_dir: Path | str | None = None,
        ttl_seconds: int = 604800,  # 7 days default
    ) -> None:
        self._base = Path(base_dir) if base_dir else DEFAULT_HANDOFF_DIR
        self._packages_dir = self._base / "packages"
        self._db_path = self._base / "handoff.db"
        self._ttl = ttl_seconds
        self._serializer = JsonSerializer()

        self._ensure_dirs()
        self._init_db()

    def _ensure_dirs(self) -> None:
        """Create storage directories with restrictive permissions."""
        self._base.mkdir(parents=True, exist_ok=True)
        self._packages_dir.mkdir(parents=True, exist_ok=True)
        # Restrict to owner only (0700)
        os.chmod(self._base, 0o700)
        os.chmod(self._packages_dir, 0o700)

    def _init_db(self) -> None:
        """Initialize SQLite schema."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS packages (
                    package_id TEXT PRIMARY KEY,
                    source_agent TEXT NOT NULL,
                    target_agent TEXT,
                    task_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    file_path TEXT NOT NULL,
                    summary TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_status ON packages(status)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_source ON packages(source_agent)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event TEXT NOT NULL,
                    package_id TEXT,
                    details TEXT
                )
            """)
            conn.commit()

    def _package_path(self, package_id: str) -> Path:
        """Get filesystem path for a package JSON file."""
        return self._packages_dir / f"{package_id}.json"

    async def save(self, package: ContextPackage) -> None:
        """Persist a context package to local storage."""
        payload = self._serializer.serialize(package).decode("utf-8")
        file_path = self._package_path(package.meta.package_id)

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(payload)
        os.chmod(file_path, 0o600)

        # Determine TTL
        expires = package.meta.expires_at
        if expires is None:
            expires = utc_now() + timedelta(seconds=self._ttl)

        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO packages
                (package_id, source_agent, target_agent, task_id, reason,
                 status, created_at, expires_at, file_path, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                package.meta.package_id,
                package.meta.source.agent_id,
                "",  # target_agent not known at save time
                package.task.original_task_id,
                package.meta.handoff_reason.value,
                "pending",
                package.meta.created_at.isoformat(),
                expires.isoformat() if expires else None,
                str(file_path),
                package.task.progress_summary.to_markdown()[:500],
            ))
            conn.commit()

        self._audit("saved", package.meta.package_id)

    async def load(self, package_id: str) -> ContextPackage | None:
        """Retrieve a context package by ID.

        Returns None if the package does not exist or has expired.
        """
        file_path = self._package_path(package_id)
        if not file_path.exists():
            return None

        # Check SQLite index for expiry before deserializing
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT expires_at FROM packages WHERE package_id = ?",
                (package_id,),
            ).fetchone()
            if row is None:
                # Index entry missing but file exists — stale file
                file_path.unlink(missing_ok=True)
                return None
            expires_at = row[0]
            if expires_at is not None:
                expires = datetime.fromisoformat(expires_at)
                if utc_now() > expires:
                    # Expired — clean up
                    file_path.unlink(missing_ok=True)
                    conn.execute(
                        "DELETE FROM packages WHERE package_id = ?",
                        (package_id,),
                    )
                    conn.commit()
                    self._audit("expired_access", package_id)
                    return None

        with open(file_path, "r", encoding="utf-8") as f:
            payload = f.read()

        return self._serializer.deserialize(payload.encode("utf-8"))

    async def delete(self, package_id: str) -> bool:
        """Delete a package and its index entry."""
        file_path = self._package_path(package_id)
        deleted = False

        if file_path.exists():
            file_path.unlink()
            deleted = True

        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM packages WHERE package_id = ?", (package_id,)
            )
            conn.commit()
            if cursor.rowcount > 0:
                deleted = True

        if deleted:
            self._audit("deleted", package_id)
        return deleted

    async def list_packages(
        self,
        status: str | None = None,
        source_agent: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List packages with optional filtering."""
        query = "SELECT * FROM packages WHERE 1=1"
        params: list[Any] = []

        if status:
            query += " AND status = ?"
            params.append(status)
        if source_agent:
            query += " AND source_agent = ?"
            params.append(source_agent)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]

    async def cleanup_expired(self) -> int:
        """Remove expired packages."""
        now = utc_now().isoformat()

        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT package_id, file_path FROM packages WHERE expires_at < ?",
                (now,),
            ).fetchall()

            count = 0
            for row in rows:
                path = Path(row["file_path"])
                if path.exists():
                    path.unlink()
                conn.execute(
                    "DELETE FROM packages WHERE package_id = ?",
                    (row["package_id"],),
                )
                count += 1
                self._audit("expired_cleanup", row["package_id"], conn=conn)

            conn.commit()
            return count

    def _audit(
        self,
        event: str,
        package_id: str | None = None,
        details: str = "",
        conn: sqlite3.Connection | None = None,
    ) -> None:
        """Write audit log entry.

        Args:
            event: Audit event name.
            package_id: Related package ID.
            details: Additional details.
            conn: Optional existing SQLite connection. If provided, writes
                through this connection instead of opening a new one.
        """
        if conn is not None:
            conn.execute("""
                INSERT INTO audit_log (timestamp, event, package_id, details)
                VALUES (?, ?, ?, ?)
            """, (
                utc_now().isoformat(),
                event,
                package_id,
                details,
            ))
            return

        with sqlite3.connect(self._db_path) as inner:
            inner.execute("""
                INSERT INTO audit_log (timestamp, event, package_id, details)
                VALUES (?, ?, ?, ?)
            """, (
                utc_now().isoformat(),
                event,
                package_id,
                details,
            ))
            inner.commit()

    async def get_audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent audit log entries."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
