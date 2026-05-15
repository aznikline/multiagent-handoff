"""Redis-backed storage for context packages."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from handoff.models.package import ContextPackage
from handoff.orchestrator.store import HandoffStore, StoreError
from handoff.serialization.serializer import JsonSerializer


class RedisHandoffStore(HandoffStore):
    """Production-grade Redis store with TTL support.

    Requires ``redis[hiredis]>=5.0``.
    """

    def __init__(
        self,
        redis_client: Any,
        key_prefix: str = "handoff:package:",
        ttl_seconds: int = 3600,
    ) -> None:
        super().__init__()
        self._redis = redis_client
        self._prefix = key_prefix
        self._ttl = ttl_seconds
        self._serializer = JsonSerializer()

    def _key(self, package_id: str) -> str:
        return f"{self._prefix}{package_id}"

    async def save(self, package: ContextPackage) -> None:
        try:
            payload = self._serializer.serialize(package).decode("utf-8")
            key = self._key(package.meta.package_id)
            # Use package TTL if set, otherwise default
            ttl = self._ttl
            if package.meta.expires_at is not None:
                ttl = int(
                    (package.meta.expires_at - datetime.utcnow()).total_seconds()
                )
                if ttl <= 0:
                    raise StoreError("Package has already expired")
            await self._redis.setex(key, ttl, payload)
        except Exception as exc:
            raise StoreError(f"Redis save failed: {exc}") from exc

    async def load(self, package_id: str) -> ContextPackage | None:
        try:
            key = self._key(package_id)
            data = await self._redis.get(key)
            if data is None:
                return None
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            return self._serializer.deserialize(data.encode("utf-8"))
        except Exception as exc:
            raise StoreError(f"Redis load failed: {exc}") from exc

    async def delete(self, package_id: str) -> bool:
        try:
            key = self._key(package_id)
            result: int = await self._redis.delete(key)
            return result > 0
        except Exception as exc:
            raise StoreError(f"Redis delete failed: {exc}") from exc

    async def list_expired(self) -> list[str]:
        """Redis handles TTL expiry automatically; this returns empty."""
        return []
