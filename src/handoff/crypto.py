"""Encryption-at-rest for context packages.

Uses AES-256-GCM via the ``cryptography`` library.
"""

from __future__ import annotations

import base64
import os
from typing import TYPE_CHECKING, Final

# cryptography is an optional dependency
_NONCE_SIZE: Final = 12  # 96 bits for GCM
_TAG_SIZE: Final = 16    # 128 bits for GCM

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM


def _get_backend() -> type["_AESGCM"]:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM
    except ImportError as exc:
        raise ImportError(
            "Encryption requires 'cryptography'. Install with: "
            "pip install agent-context-handoff[crypto]"
        ) from exc


def generate_key() -> bytes:
    """Generate a new 256-bit AES key.

    Returns:
        32-byte random key.
    """
    return os.urandom(32)


def encrypt(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt plaintext with AES-256-GCM.

    Args:
        plaintext: Data to encrypt.
        key: 32-byte AES key.

    Returns:
        nonce + ciphertext + tag (all concatenated).
    """
    AESGCM = _get_backend()
    nonce = os.urandom(_NONCE_SIZE)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    # ciphertext already includes the auth tag at the end
    return nonce + ciphertext


def decrypt(ciphertext: bytes, key: bytes) -> bytes:
    """Decrypt ciphertext with AES-256-GCM.

    Args:
        ciphertext: nonce + ciphertext + tag (as returned by encrypt).
        key: 32-byte AES key.

    Returns:
        Original plaintext.

    Raises:
        ValueError: If authentication fails (tampered data).
    """
    AESGCM = _get_backend()
    if len(ciphertext) < _NONCE_SIZE + _TAG_SIZE:
        raise ValueError("Ciphertext too short")
    nonce = ciphertext[:_NONCE_SIZE]
    encrypted = ciphertext[_NONCE_SIZE:]
    aesgcm = AESGCM(key)
    try:
        return aesgcm.decrypt(nonce, encrypted, None)
    except Exception as exc:
        raise ValueError(f"Authentication failed: {exc}") from exc


def encrypt_to_b64(plaintext: bytes, key: bytes) -> str:
    """Encrypt and base64-encode for JSON embedding."""
    return base64.b64encode(encrypt(plaintext, key)).decode("ascii")


def decrypt_from_b64(ciphertext_b64: str, key: bytes) -> bytes:
    """Base64-decode and decrypt."""
    ciphertext = base64.b64decode(ciphertext_b64)
    return decrypt(ciphertext, key)
