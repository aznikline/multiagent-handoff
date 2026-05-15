"""Storage backends for serialized context packages."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from handoff._utils import utc_now

from handoff.models.package import ContextPackage
from handoff.serialization.serializer import JsonSerializer, SerializationError


class StoreError(Exception):
    """Raised when a store operation fails."""

    pass


class HandoffStore(ABC):
    """Abstract storage backend for context packages."""

    def __init__(self) -> None:
        self._serializer = JsonSerializer()

    @abstractmethod
    async def save(self, package: ContextPackage) -> None:
        """Persist a context package.

        Args:
            package: The package to store.

        Raises:
            StoreError: If persistence fails.
        """
        raise NotImplementedError

    @abstractmethod
    async def load(self, package_id: str) -> ContextPackage | None:
        """Retrieve a context package by ID.

        Args:
            package_id: The unique package identifier.

        Returns:
            The deserialized package, or None if not found/expired.

        Raises:
            StoreError: If retrieval fails.
        """
        raise NotImplementedError

    @abstractmethod
    async def delete(self, package_id: str) -> bool:
        """Delete a context package.

        Args:
            package_id: The package to delete.

        Returns:
            True if the package existed and was deleted.
        """
        raise NotImplementedError

    @abstractmethod
    async def list_expired(self) -> list[str]:
        """List package IDs that have exceeded their TTL.

        Returns:
            List of expired package IDs.
        """
        raise NotImplementedError

    def _serialize(self, package: ContextPackage) -> bytes:
        return self._serializer.serialize(package)

    def _deserialize(self, data: bytes) -> ContextPackage:
        return self._serializer.deserialize(data)


class InMemoryHandoffStore(HandoffStore):
    """In-memory store with TTL support.

    Suitable for Phase 1 MVP and testing. Not persistent across restarts.
    """

    def __init__(self) -> None:
        super().__init__()
        self._data: dict[str, tuple[bytes, datetime | None]] = {}

    async def save(self, package: ContextPackage) -> None:
        try:
            payload = self._serialize(package)
            expires = package.meta.expires_at
            self._data[package.meta.package_id] = (payload, expires)
        except SerializationError as exc:
            raise StoreError(f"Failed to serialize package: {exc}") from exc

    async def load(self, package_id: str) -> ContextPackage | None:
        entry = self._data.get(package_id)
        if entry is None:
            return None

        payload, expires = entry
        if expires is not None and utc_now() > expires:
            # Auto-cleanup on access
            del self._data[package_id]
            return None

        try:
            return self._deserialize(payload)
        except SerializationError as exc:
            raise StoreError(f"Failed to deserialize package: {exc}") from exc

    async def delete(self, package_id: str) -> bool:
        if package_id in self._data:
            del self._data[package_id]
            return True
        return False

    async def list_expired(self) -> list[str]:
        now = utc_now()
        expired = [
            pid for pid, (_, expires) in self._data.items()
            if expires is not None and now > expires
        ]
        return expired
