"""SecretBox round-trips."""

from __future__ import annotations

import pytest
from nacl.secret import SecretBox

from viewlift_ai.crypto import SecretBoxCipher


def test_encrypt_decrypt_bytes() -> None:
    key = b"k" * SecretBox.KEY_SIZE
    cipher = SecretBoxCipher(key)
    plaintext = b'{"token":"secret"}'
    ciphertext = cipher.encrypt(plaintext)
    assert ciphertext != plaintext
    assert cipher.decrypt(ciphertext) == plaintext


def test_encrypt_decrypt_headers() -> None:
    key = b"h" * SecretBox.KEY_SIZE
    cipher = SecretBoxCipher(key)
    headers = {"Authorization": "Bearer x", "Content-Type": "application/json"}
    blob = cipher.encrypt_headers(headers)
    assert b"Bearer" not in blob
    assert cipher.decrypt_headers(blob) == headers


def test_rejects_bad_key_size() -> None:
    with pytest.raises(ValueError):
        SecretBoxCipher(b"short")
