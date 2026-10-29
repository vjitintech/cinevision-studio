"""Encrypted blob store.

Paths are derived only from server-generated UUIDs (`blobs/<aa>/<uuid>.cve`) and verified to resolve inside
the storage root, so no client-supplied value ever reaches the filesystem.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

from . import db
from .config import settings
from .crypto import EncryptedReader, encrypt_stream
from .keys import keyring


class StorageError(Exception):
    pass


def blob_path(blob_id: str) -> Path:
    if not db.is_uuid(blob_id):
        raise StorageError("invalid blob id")
    root = settings().blob_dir.resolve()
    p = (root / blob_id[:2] / f"{blob_id}.cve").resolve()
    if root not in p.parents:
        raise StorageError("path escapes storage root")
    return p


def put_stream(src: BinaryIO, *, owner_id: str, project_id: str, kind: str, content_type: str) -> str:
    blob_id = db.new_id()
    dek, wrapped = keyring().new_wrapped_dek(blob_id)
    path = blob_path(blob_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".partial")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as dst:
            size, digest = encrypt_stream(src, dst, dek, blob_id)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    db.run(
        "INSERT INTO blobs(id,owner_id,project_id,kind,content_type,wrapped_dek,plaintext_size,sha256,created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (blob_id, owner_id, project_id, kind, content_type, wrapped, size, digest, db.now_iso()),
    )
    return blob_id


def put_file(path: Path, **kw) -> str:
    with open(path, "rb") as f:
        return put_stream(f, **kw)


def put_bytes(data: bytes, **kw) -> str:
    import io
    return put_stream(io.BytesIO(data), **kw)


def reader(blob_row) -> EncryptedReader:
    dek = keyring().unwrap_dek(blob_row["wrapped_dek"], blob_row["id"])
    return EncryptedReader(blob_path(blob_row["id"]), dek, blob_row["id"], blob_row["plaintext_size"])


def read_bytes(blob_row) -> bytes:
    return reader(blob_row).read_all() if blob_row["plaintext_size"] else b""


@contextmanager
def private_tempdir(prefix: str = "job-") -> Iterator[Path]:
    """Job-private plaintext scratch space; always removed, even on failure."""
    root = settings().tmp_dir
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    d = Path(tempfile.mkdtemp(prefix=prefix, dir=root))
    os.chmod(d, 0o700)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def decrypt_to_file(blob_row, dest: Path) -> Path:
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        if blob_row["plaintext_size"]:
            reader(blob_row).decrypt_to(f)
    return dest


def destroy_blob_file(blob_id: str) -> None:
    try:
        p = blob_path(blob_id)
    except StorageError:
        return
    p.unlink(missing_ok=True)
    try:
        p.parent.rmdir()
    except OSError:
        pass
