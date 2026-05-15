"""Encrypted serializer wrapper for at-rest protection."""

from __future__ import annotations

from handoff.crypto import decrypt_from_b64, encrypt_to_b64
from handoff.models.package import ContextPackage
from handoff.serialization.serializer import JsonSerializer, SerializationError


class EncryptedSerializer(JsonSerializer):
    """JSON serializer with AES-256-GCM encryption.

    The serialized JSON is encrypted before being returned as bytes,
    and decrypted during deserialization.
    """

    def __init__(self, key: bytes) -> None:
        self._key = key
        super().__init__()

    def serialize(self, package: ContextPackage) -> bytes:
        """Serialize and encrypt a package."""
        json_bytes = super().serialize(package)
        encrypted_b64 = encrypt_to_b64(json_bytes, self._key)
        return encrypted_b64.encode("utf-8")

    def deserialize(self, data: bytes) -> ContextPackage:
        """Decrypt and deserialize a package."""
        try:
            encrypted_b64 = data.decode("utf-8")
            json_bytes = decrypt_from_b64(encrypted_b64, self._key)
            return super().deserialize(json_bytes)
        except ValueError as exc:
            raise SerializationError(f"Decryption failed: {exc}") from exc
