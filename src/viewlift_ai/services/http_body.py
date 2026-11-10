"""Decode Content-Encoding on a response body. Callers choose how to report failure."""

from __future__ import annotations

import gzip
import zlib
from collections.abc import Mapping


class BodyDecodeError(Exception):
    """The bytes claim a compression the decoder could not undo."""


def decode_content_encoding(raw: bytes, headers: Mapping[str, str]) -> bytes:
    """Turn gzip, deflate, or brotli into the bytes the page can show.

    No Content-Encoding and no gzip magic means the bytes are already plain.
    """
    encoding = _header(headers, "content-encoding").split(",")[0].strip().lower()
    if encoding in {"gzip", "x-gzip"} or raw[:2] == b"\x1f\x8b":
        return _gunzip(raw)
    if encoding == "deflate":
        return _inflate(raw)
    if encoding in {"br", "brotli"}:
        return _brotli(raw)
    if encoding == "zstd" or raw[:4] == b"\x28\xb5\x2f\xfd":
        raise BodyDecodeError(
            "Response is zstd-compressed. Remove Accept-Encoding from the headers."
        )
    return raw


def _header(headers: Mapping[str, str], name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return ""


def _gunzip(raw: bytes) -> bytes:
    try:
        return gzip.decompress(raw)
    except (OSError, EOFError, zlib.error):
        if raw.lstrip()[:1] in {b"{", b"["}:
            return raw
        raise BodyDecodeError("Response was gzip but could not be decoded.") from None


def _inflate(raw: bytes) -> bytes:
    for window in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            return zlib.decompress(raw, window)
        except zlib.error:
            continue
    if raw.lstrip()[:1] in {b"{", b"["}:
        return raw
    raise BodyDecodeError("Response was deflate but could not be decoded.")


def _brotli(raw: bytes) -> bytes:
    try:
        import brotli
    except ImportError as exc:
        raise BodyDecodeError(
            "Response is brotli-compressed. Remove Accept-Encoding from the headers."
        ) from exc
    try:
        decoded = brotli.decompress(raw)
    except Exception as exc:
        raise BodyDecodeError("Response was brotli but could not be decoded.") from exc
    if not isinstance(decoded, bytes):
        raise BodyDecodeError("Response was brotli but could not be decoded.")
    return decoded
