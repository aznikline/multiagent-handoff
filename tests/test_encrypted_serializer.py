"""Tests for encrypted serializer."""

from __future__ import annotations

import pytest

from handoff.crypto import generate_key
from handoff.models.package import ContextPackage, PackageMeta, SourceInfo
from handoff.models.task import HandoffReason, TaskInfo
from handoff.serialization.encrypted_serializer import EncryptedSerializer
from handoff.serialization.serializer import SerializationError


class TestEncryptedSerializer:
    """AES-256-GCM encrypted serializer tests."""

    @pytest.fixture
    def key(self) -> bytes:
        return generate_key()

    @pytest.fixture
    def sample_package(self) -> ContextPackage:
        return ContextPackage(
            meta=PackageMeta(
                source=SourceInfo(agent_id="agent-1"),
                handoff_reason=HandoffReason.USER_TRIGGERED,
            ),
            task=TaskInfo(original_task_id="task-1", description="Test"),
        )

    def test_round_trip(self, key: bytes, sample_package: ContextPackage) -> None:
        serializer = EncryptedSerializer(key)
        data = serializer.serialize(sample_package)
        # Should be base64-encoded encrypted data, not plaintext JSON
        assert b"spec_version" not in data
        assert b"agent-1" not in data
        restored = serializer.deserialize(data)
        assert restored.meta.source.agent_id == "agent-1"
        assert restored.task.original_task_id == "task-1"

    def test_wrong_key_fails(self, key: bytes, sample_package: ContextPackage) -> None:
        serializer = EncryptedSerializer(key)
        data = serializer.serialize(sample_package)
        wrong_key = generate_key()
        wrong_serializer = EncryptedSerializer(wrong_key)
        with pytest.raises(SerializationError, match="Authentication failed"):
            wrong_serializer.deserialize(data)

    def test_tampered_data_fails(self, key: bytes, sample_package: ContextPackage) -> None:
        serializer = EncryptedSerializer(key)
        data = bytearray(serializer.serialize(sample_package))
        data[-1] ^= 0xFF
        # Tampering may produce invalid UTF-8 or corrupt the ciphertext;
        # either way deserialization must fail.
        with pytest.raises(SerializationError):
            serializer.deserialize(bytes(data))
