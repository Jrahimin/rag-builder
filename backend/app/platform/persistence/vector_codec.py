"""Pack and unpack embedding vectors as BYTEA (float32 little-endian)."""

from __future__ import annotations

import struct


def pack_vector(values: list[float]) -> bytes:
    """Serialize a float vector to packed float32 bytes."""
    return struct.pack(f"<{len(values)}f", *values)


def unpack_vector(data: bytes, *, dimensions: int) -> list[float]:
    """Deserialize packed float32 bytes to a float vector."""
    expected = dimensions * 4
    if len(data) != expected:
        msg = f"Expected {expected} bytes for {dimensions} dimensions, got {len(data)}"
        raise ValueError(msg)
    return list(struct.unpack(f"<{dimensions}f", data))
