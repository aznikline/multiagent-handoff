"""Serializer implementations for ContextPackage."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import TypeVar

from handoff.models.package import ContextPackage

T = TypeVar("T")


class SerializationError(Exception):
    """Raised when serialization or deserialization fails."""

    pass


class Serializer(ABC):
    """Abstract base for context package serializers."""

    @abstractmethod
    def serialize(self, package: ContextPackage) -> bytes:
        """Serialize a ContextPackage to bytes.

        Args:
            package: The package to serialize.

        Returns:
            Serialized byte representation.

        Raises:
            SerializationError: If serialization fails.
        """
        raise NotImplementedError

    @abstractmethod
    def deserialize(self, data: bytes) -> ContextPackage:
        """Deserialize bytes to a ContextPackage.

        Args:
            data: The serialized data.

        Returns:
            Reconstructed ContextPackage.

        Raises:
            SerializationError: If deserialization fails.
        """
        raise NotImplementedError


class JsonSerializer(Serializer):
    """JSON-based serializer with forward-compatibility support.

    Unknown fields in the incoming JSON are ignored during deserialization,
    allowing forward compatibility with newer schema versions.
    """

    def serialize(self, package: ContextPackage) -> bytes:
        try:
            # Pydantic v2 model_dump_json produces clean JSON
            return package.model_dump_json(
                exclude_none=False, by_alias=False
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise SerializationError(f"Failed to serialize package: {exc}") from exc

    def deserialize(self, data: bytes) -> ContextPackage:
        try:
            payload = json.loads(data.decode("utf-8"))
            return ContextPackage.model_validate(payload)
        except json.JSONDecodeError as exc:
            raise SerializationError(f"Invalid JSON: {exc}") from exc
        except ValueError as exc:
            raise SerializationError(f"Schema validation failed: {exc}") from exc
