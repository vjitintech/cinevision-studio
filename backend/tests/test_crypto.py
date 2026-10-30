import io
import os

import pytest

from cinevision.crypto import CHUNK_SIZE, CryptoError, EncryptedReader, KeyRing, encrypt_stream


@pytest.fixture
def ring():
    return KeyRing(os.urandom(32))


@pytest.mark.parametrize("size", [0, 1, CHUNK_SIZE - 1, CHUNK_SIZE, CHUNK_SIZE + 1, 3 * CHUNK_SIZE + 17])
def test_chunked_roundtrip_and_ranges(tmp_path, ring, size):
    data = os.urandom(size)
    dek, _ = ring.new_wrapped_dek("b1")
    p = tmp_path / "x.cve"
    with open(p, "wb") as f:
        n, _ = encrypt_stream(io.BytesIO(data), f, dek, "b1")
    assert n == size
    assert size < 16 or data[:64] not in p.read_bytes()   # tiny inputs can appear in ciphertext by chance
    r = EncryptedReader(p, dek, "b1", size)
    assert (r.read_all() if size else b"") == data
    if size > 10:
        for a, b in [(0, 9), (size - 5, size - 1), (CHUNK_SIZE - 3, min(size - 1, CHUNK_SIZE + 3))]:
            assert b"".join(r.iter_range(a, b)) == data[a:b + 1]


def test_tamper_and_truncation_detected(tmp_path, ring):
    data = os.urandom(2 * CHUNK_SIZE + 10)
    dek, _ = ring.new_wrapped_dek("b2")
    p = tmp_path / "x.cve"
    with open(p, "wb") as f:
        encrypt_stream(io.BytesIO(data), f, dek, "b2")
    raw = bytearray(p.read_bytes())
    raw[100] ^= 1
    p.write_bytes(bytes(raw))
    with pytest.raises(CryptoError):
        EncryptedReader(p, dek, "b2", len(data)).read_all()
    # truncation: drop last chunk and claim smaller size
    raw[100] ^= 1  # undo tamper
    p.write_bytes(bytes(raw[: 16 + 2 * (CHUNK_SIZE + 16)]))
    with pytest.raises(CryptoError):
        EncryptedReader(p, dek, "b2", 2 * CHUNK_SIZE).read_all()


def test_blob_id_binding(tmp_path, ring):
    dek, wrapped = ring.new_wrapped_dek("blob-a")
    with pytest.raises(CryptoError):
        ring.unwrap_dek(wrapped, "blob-b")
    assert ring.unwrap_dek(wrapped, "blob-a") == dek


def test_field_aad_binding(ring):
    ct = ring.enc("Secret title", "projects:title:1")
    assert ring.dec(ct, "projects:title:1") == "Secret title"
    with pytest.raises(CryptoError):
        ring.dec(ct, "projects:title:2")


def test_media_token(ring):
    t = ring.media_token("b", "u", "s", ttl_s=60)
    assert ring.verify_media_token(t, "b", "u", "s")
    assert not ring.verify_media_token(t, "b", "other-user", "s")
    assert not ring.verify_media_token(t, "b", "u", "other-session")
    expired = ring.media_token("b", "u", "s", ttl_s=-1)
    assert not ring.verify_media_token(expired, "b", "u", "s")
