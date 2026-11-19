"""Random values, hashing and refresh token encryption for sessions.

Session ids and login states are 256-bit values from `secrets`. Only their SHA-256 is
stored. Refresh tokens are encrypted with AES-256-GCM under a key ring: the first key
encrypts, every key decrypts, so keys can be rotated without ending sessions.
"""

import base64
import binascii
import hashlib
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12


def random_token() -> str:
    """256 bits from a cryptographically secure generator, URL-safe."""
    return secrets.token_urlsafe(32)


def token_hash(value: str) -> bytes:
    return hashlib.sha256(value.encode("ascii")).digest()


def pkce_challenge(verifier: str) -> str:
    """S256 code challenge (RFC 7636 4.2)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class KeyRingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Sealed:
    ciphertext: bytes
    key_id: str


class KeyRing:
    """AES-256-GCM keys by id. The associated data binds a ciphertext to its session."""

    def __init__(self, spec: str) -> None:
        keys: dict[str, AESGCM] = {}
        order: list[str] = []
        for raw in (part.strip() for part in spec.split(",")):
            if not raw:
                continue
            key_id, sep, encoded = raw.partition(":")
            if not sep or not key_id:
                raise KeyRingError("each session key must be 'key_id:base64key'")
            try:
                material = base64.b64decode(encoded, validate=True)
            except binascii.Error as exc:
                raise KeyRingError(f"session key {key_id!r} is not valid base64") from exc
            if len(material) != 32:
                raise KeyRingError(f"session key {key_id!r} must be 32 bytes")
            if key_id in keys:
                raise KeyRingError(f"session key {key_id!r} appears twice")
            keys[key_id] = AESGCM(material)
            order.append(key_id)
        if not order:
            raise KeyRingError("at least one session key is required")
        self._keys = keys
        self._active = order[0]

    def seal(self, plaintext: str, associated: bytes) -> Sealed:
        nonce = secrets.token_bytes(NONCE_BYTES)
        sealed = self._keys[self._active].encrypt(nonce, plaintext.encode("utf-8"), associated)
        return Sealed(nonce + sealed, self._active)

    def open(self, sealed: Sealed, associated: bytes) -> str:
        key = self._keys.get(sealed.key_id)
        if key is None:
            raise KeyRingError(f"session key {sealed.key_id!r} is no longer configured")
        nonce, body = sealed.ciphertext[:NONCE_BYTES], sealed.ciphertext[NONCE_BYTES:]
        return key.decrypt(nonce, body, associated).decode("utf-8")
