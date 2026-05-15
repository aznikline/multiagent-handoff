"""Tests for encryption and migration modules."""

from __future__ import annotations

import pytest

from handoff.crypto import decrypt, decrypt_from_b64, encrypt, encrypt_to_b64, generate_key
from handoff.migrations import migrate, register_migration


class TestCrypto:
    """AES-256-GCM encryption tests."""

    def test_generate_key_length(self) -> None:
        key = generate_key()
        assert len(key) == 32

    def test_round_trip(self) -> None:
        key = generate_key()
        plaintext = b"Hello, secret world!"
        ciphertext = encrypt(plaintext, key)
        assert ciphertext != plaintext
        assert len(ciphertext) > len(plaintext) + 12  # nonce + tag
        decrypted = decrypt(ciphertext, key)
        assert decrypted == plaintext

    def test_different_keys_fail(self) -> None:
        key1 = generate_key()
        key2 = generate_key()
        ciphertext = encrypt(b"secret", key1)
        with pytest.raises(ValueError):
            decrypt(ciphertext, key2)

    def test_tampered_data_fails(self) -> None:
        key = generate_key()
        ciphertext = bytearray(encrypt(b"secret", key))
        ciphertext[-1] ^= 0xFF  # Flip last bit
        with pytest.raises(ValueError):
            decrypt(bytes(ciphertext), key)

    def test_b64_round_trip(self) -> None:
        key = generate_key()
        plaintext = b"json data here"
        b64 = encrypt_to_b64(plaintext, key)
        assert isinstance(b64, str)
        decrypted = decrypt_from_b64(b64, key)
        assert decrypted == plaintext


class TestMigrations:
    """Schema migration tests."""

    def test_no_migration_needed(self) -> None:
        data = {"meta": {"spec_version": "1.0"}, "task": {}}
        result = migrate(data, target_version="1.0")
        assert result["meta"]["spec_version"] == "1.0"

    def test_migration_1_0_to_1_1(self) -> None:
        data = {"meta": {"spec_version": "1.0"}}
        result = migrate(data, target_version="1.1")
        assert result["meta"]["spec_version"] == "1.1"
        assert result["security"]["classification"] == "internal"

    def test_migration_preserves_existing_security(self) -> None:
        data = {
            "meta": {"spec_version": "1.0"},
            "security": {"classification": "confidential"},
        }
        result = migrate(data, target_version="1.1")
        assert result["security"]["classification"] == "confidential"

    def test_unknown_migration_raises(self) -> None:
        data = {"meta": {"spec_version": "0.9"}}
        with pytest.raises(ValueError, match="No migration path"):
            migrate(data, target_version="1.0")

    def test_custom_migration(self) -> None:
        @register_migration("1.1", "2.0")
        def migrate_v1_1_to_2_0(data: dict) -> dict:
            data["meta"]["spec_version"] = "2.0"
            data["new_feature"] = True
            return data

        data = {"meta": {"spec_version": "1.1"}}
        result = migrate(data, target_version="2.0")
        assert result["meta"]["spec_version"] == "2.0"
        assert result["new_feature"] is True
