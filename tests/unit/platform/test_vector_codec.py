"""Unit tests for vector codec."""

from __future__ import annotations

import pytest

from app.platform.persistence.vector_codec import pack_vector, unpack_vector


@pytest.mark.unit
def test_pack_unpack_roundtrip() -> None:
    values = [0.1, -0.2, 0.3, 0.0]
    packed = pack_vector(values)
    restored = unpack_vector(packed, dimensions=len(values))
    assert restored == pytest.approx(values)
