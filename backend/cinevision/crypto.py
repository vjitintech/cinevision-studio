"""Encryption primitives.

Key hierarchy (all derived with HKDF-SHA256 from the 32-byte master key, which lives outside the data volume):

    master ─┬─ kek     wraps per-file data keys (DEKs)
            ├─ field   encrypts sensitive DB columns
            ├─ token   HMAC for short-lived media tokens
            └─ ident   keyed hash for IP addresses in audit logs

Files use a chunked AES-256-GCM format so that HTTP Range requests can decrypt only what is needed:

    "CVE1" | chunk_size:u32 | nonce_prefix:8B | chunk_0 | chunk_1 | ...
    chunk_i = AES-GCM(dek, nonce=prefix‖u32(i), aad=blob_id‖u32(i)‖last_flag) — ciphertext+16B tag

Including the index and a last-chunk flag in the AAD prevents chunk reordering and truncation.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import time
from pathlib import Path
from typing import BinaryIO, Iterator

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"CVE1"
HEADER_LEN = 4 + 4 + 8
CHUNK_SIZE = 1024 * 1024
TAG_LEN = 16


class CryptoError(Exception):
    pass


def _hkdf(master: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"cinevision-v1", info=info).derive(master)


class KeyRing:
    def __init__(self, master_key: bytes):
        if len(master_key) != 32:
            raise CryptoError("master key must be 32 bytes")
        self.kek = _hkdf(master_key, b"kek")
        self.field = _hkdf(master_key, b"field")
        self.token = _hkdf(master_key, b"token")
        self.ident = _hkdf(master_key, b"ident")

    # ---- DEK wrapping -------------------------------------------------
    def new_wrapped_dek(self, blob_id: str) -> tuple[bytes, str]:
        dek = AESGCM.generate_key(bit_length=256)
        return dek, self.wrap_dek(dek, blob_id)

    def wrap_dek(self, dek: bytes, blob_id: str) -> str:
        nonce = os.urandom(12)
        ct = AESGCM(self.kek).encrypt(nonce, dek, b"dek:" + blob_id.encode())
        return base64.b64encode(nonce + ct).decode()

    def unwrap_dek(self, wrapped: str, blob_id: str) -> bytes:
        raw = base64.b64decode(wrapped)
        try:
            return AESGCM(self.kek).decrypt(raw[:12], raw[12:], b"dek:" + blob_id.encode())
        except Exception as e:  # InvalidTag
            raise CryptoError("cannot unwrap data key") from e

    # ---- field encryption --------------------------------------------
    def enc(self, value: str | None, aad: str) -> str | None:
        if value is None:
            return None
        nonce = os.urandom(12)
        ct = AESGCM(self.field).encrypt(nonce, value.encode("utf-8"), aad.encode())
        return base64.b64encode(nonce + ct).decode()

    def dec(self, value: str | None, aad: str) -> str | None:
        if value is None:
            return None
        raw = base64.b64decode(value)
        try:
            return AESGCM(self.field).decrypt(raw[:12], raw[12:], aad.encode()).decode("utf-8")
        except Exception as e:
            raise CryptoError(f"field decryption failed ({aad.split(':')[0]})") from e

    def enc_json(self, obj, aad: str) -> str:
        return self.enc(json.dumps(obj, separators=(",", ":"), ensure_ascii=False), aad)

    def dec_json(self, value: str | None, aad: str):
        if value is None:
            return None
        return json.loads(self.dec(value, aad))

    # ---- tokens & identifiers ----------------------------------------
    def media_token(self, blob_id: str, user_id: str, session_hash: str, ttl_s: int = 300) -> str:
        exp = int(time.time()) + ttl_s
        msg = f"{blob_id}|{user_id}|{session_hash}|{exp}".encode()
        sig = hmac.new(self.token, msg, hashlib.sha256).digest()
        return f"{exp}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"

    def verify_media_token(self, token: str, blob_id: str, user_id: str, session_hash: str) -> bool:
        try:
            exp_s, sig_s = token.split(".", 1)
            exp = int(exp_s)
        except (ValueError, AttributeError):
            return False
        if exp < time.time():
            return False
        msg = f"{blob_id}|{user_id}|{session_hash}|{exp}".encode()
        expected = base64.urlsafe_b64encode(hmac.new(self.token, msg, hashlib.sha256).digest()).decode().rstrip("=")
        return hmac.compare_digest(expected, sig_s)

    def ident_hash(self, value: str | None) -> str | None:
        if not value:
            return None
        return hmac.new(self.ident, value.encode(), hashlib.sha256).hexdigest()[:32]


# ---- chunked file encryption -------------------------------------------

def _chunk_aad(blob_id: str, idx: int, last: bool) -> bytes:
    return blob_id.encode() + struct.pack(">I?", idx, last)


def _nonce(prefix: bytes, idx: int) -> bytes:
    return prefix + struct.pack(">I", idx)


def encrypt_stream(src: BinaryIO, dst: BinaryIO, dek: bytes, blob_id: str, chunk_size: int = CHUNK_SIZE) -> tuple[int, str]:
    """Encrypt src into dst. Returns (plaintext_size, sha256 hex)."""
    aes = AESGCM(dek)
    prefix = os.urandom(8)
    dst.write(MAGIC + struct.pack(">I", chunk_size) + prefix)
    h = hashlib.sha256()
    total = 0
    idx = 0
    cur = src.read(chunk_size)
    while True:
        nxt = src.read(chunk_size) if cur else b""
        last = not nxt
        h.update(cur)
        total += len(cur)
        dst.write(aes.encrypt(_nonce(prefix, idx), cur, _chunk_aad(blob_id, idx, last)))
        if last:
            break
        cur = nxt
        idx += 1
    return total, h.hexdigest()


class EncryptedReader:
    """Random-access reader over a chunked encrypted file."""

    def __init__(self, path: Path, dek: bytes, blob_id: str, plaintext_size: int):
        self.path = path
        self.aes = AESGCM(dek)
        self.blob_id = blob_id
        self.size = plaintext_size
        with open(path, "rb") as f:
            hdr = f.read(HEADER_LEN)
        if len(hdr) != HEADER_LEN or hdr[:4] != MAGIC:
            raise CryptoError("not an encrypted blob")
        self.chunk_size = struct.unpack(">I", hdr[4:8])[0]
        self.prefix = hdr[8:16]
        self.n_chunks = max(1, -(-plaintext_size // self.chunk_size))

    def _read_chunk(self, f: BinaryIO, idx: int) -> bytes:
        enc_chunk = self.chunk_size + TAG_LEN
        f.seek(HEADER_LEN + idx * enc_chunk)
        data = f.read(enc_chunk)
        last = idx == self.n_chunks - 1
        try:
            return self.aes.decrypt(_nonce(self.prefix, idx), data, _chunk_aad(self.blob_id, idx, last))
        except Exception as e:
            raise CryptoError("blob integrity check failed") from e

    def iter_range(self, start: int, end_inclusive: int) -> Iterator[bytes]:
        if self.size == 0:
            return
        start = max(0, start)
        end_inclusive = min(end_inclusive, self.size - 1)
        first, last = start // self.chunk_size, end_inclusive // self.chunk_size
        with open(self.path, "rb") as f:
            for idx in range(first, last + 1):
                chunk = self._read_chunk(f, idx)
                base = idx * self.chunk_size
                lo = max(start - base, 0)
                hi = min(end_inclusive - base + 1, len(chunk))
                yield chunk[lo:hi]

    def read_all(self) -> bytes:
        return b"".join(self.iter_range(0, self.size - 1))

    def decrypt_to(self, dst: BinaryIO) -> None:
        for part in self.iter_range(0, self.size - 1):
            dst.write(part)
