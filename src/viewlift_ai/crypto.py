"""PyNaCl SecretBox helpers for at-rest encryption."""

from __future__ import annotations

import json
from collections.abc import Mapping

from nacl.secret import SecretBox
from nacl.utils import random


class SecretBoxCipher:
    """Encrypt/decrypt sensitive payloads with a 32-byte key."""

    def __init__(self, key: bytes) -> None:
        if len(key) != SecretBox.KEY_SIZE:
            msg = f"SecretBox key must be {SecretBox.KEY_SIZE} bytes"
            raise ValueError(msg)
        self._box = SecretBox(key)

    def encrypt(self, plaintext: bytes) -> bytes:
        return bytes(self._box.encrypt(plaintext, random(SecretBox.NONCE_SIZE)))

    def decrypt(self, ciphertext: bytes) -> bytes:
        return bytes(self._box.decrypt(ciphertext))

    def encrypt_headers(self, headers: Mapping[str, str]) -> bytes:
        payload = json.dumps(dict(headers), separators=(",", ":"), sort_keys=True).encode("utf-8")
        return self.encrypt(payload)

    def decrypt_headers(self, ciphertext: bytes) -> dict[str, str]:
        if not ciphertext:
            return {}
        raw = self.decrypt(ciphertext)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            msg = "decrypted headers must be an object"
            raise TypeError(msg)
        return {str(k): str(v) for k, v in data.items()}
