#!/usr/bin/env python3
"""Encrypted backups, using a key that is separate from the master key.

    python scripts/backup.py create  --data ./data --out ./backups --key-file deploy/secrets/backup_key [--keep-days 30 --keep-min 3]
    python scripts/backup.py restore --archive backups/cinevision-2026….cvbak --data ./restored --key-file deploy/secrets/backup_key
    python scripts/backup.py prune   --out ./backups --keep-days 30 --keep-min 3

What is backed up: a consistent SQLite snapshot (online backup API) + the already-encrypted blob files.
The whole archive is then encrypted again with the backup key (AES-256-GCM, chunked, authenticated), so a
leaked backup reveals nothing without BOTH keys, and project deletion (which destroys per-file keys in the DB)
does not reach into older backups — prune them on the retention schedule documented in docs/PRIVACY.md.
"""
from __future__ import annotations

import argparse
import base64
import io
import os
import sqlite3
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from cinevision.crypto import EncryptedReader, KeyRing, encrypt_stream  # noqa: E402

MAGIC = b"CVBAK1\n"


def _key(path: str) -> KeyRing:
    raw = base64.b64decode(Path(path).read_text().strip())
    return KeyRing(raw)


def create(data: Path, out: Path, key_file: str) -> Path:
    ring = _key(key_file)
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with tempfile.TemporaryDirectory() as td:
        snap = Path(td) / "cinevision.db"
        src = sqlite3.connect(data / "cinevision.db")
        dst = sqlite3.connect(snap)
        src.backup(dst)
        dst.close(); src.close()
        tar_path = Path(td) / "bundle.tar"
        with tarfile.open(tar_path, "w") as tar:
            tar.add(snap, arcname="cinevision.db")
            blobs = data / "blobs"
            if blobs.exists():
                for f in blobs.rglob("*.cve"):
                    tar.add(f, arcname=str(f.relative_to(data)))
        bid = f"backup-{stamp}"
        dek, wrapped = ring.new_wrapped_dek(bid)
        target = out / f"cinevision-{stamp}.cvbak"
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f, open(tar_path, "rb") as src_f:
            header = MAGIC + f"{bid}\n{wrapped}\n{tar_path.stat().st_size}\n".encode()
            f.write(len(header).to_bytes(4, "big") + header)
            encrypt_stream(src_f, f, dek, bid)
    print(f"backup written: {target}")
    return target


def restore(archive: Path, data: Path, key_file: str) -> None:
    ring = _key(key_file)
    raw = archive.read_bytes()
    hlen = int.from_bytes(raw[:4], "big")
    header = raw[4:4 + hlen]
    if not header.startswith(MAGIC):
        sys.exit("not a CineVision backup")
    bid, wrapped, size_s = header[len(MAGIC):].decode().strip().split("\n")
    dek = ring.unwrap_dek(wrapped, bid)
    body = archive.with_suffix(".body.tmp")
    body.write_bytes(raw[4 + hlen:])
    try:
        plain = EncryptedReader(body, dek, bid, int(size_s)).read_all()
    finally:
        body.unlink(missing_ok=True)
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(data.iterdir()):
        sys.exit(f"{data} is not empty — restore into an empty directory")
    with tarfile.open(fileobj=io.BytesIO(plain)) as tar:
        tar.extractall(data, filter="data")
    print(f"restored into {data}. Start the app with the SAME master key that was in use when the backup was made.")


def prune(out: Path, keep_days: int, keep_min: int) -> None:
    files = sorted(out.glob("cinevision-*.cvbak"), key=lambda p: p.stat().st_mtime, reverse=True)
    cutoff = time.time() - keep_days * 86400
    for p in files[keep_min:]:
        if p.stat().st_mtime < cutoff:
            p.unlink()
            print(f"pruned {p.name}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create"); c.add_argument("--data", default="data"); c.add_argument("--out", default="backups")
    c.add_argument("--key-file", required=True); c.add_argument("--keep-days", type=int, default=30); c.add_argument("--keep-min", type=int, default=3)
    r = sub.add_parser("restore"); r.add_argument("--archive", required=True); r.add_argument("--data", required=True); r.add_argument("--key-file", required=True)
    p = sub.add_parser("prune"); p.add_argument("--out", default="backups"); p.add_argument("--keep-days", type=int, default=30); p.add_argument("--keep-min", type=int, default=3)
    a = ap.parse_args()
    if a.cmd == "create":
        create(Path(a.data), Path(a.out), a.key_file)
        prune(Path(a.out), a.keep_days, a.keep_min)
    elif a.cmd == "restore":
        restore(Path(a.archive), Path(a.data), a.key_file)
    else:
        prune(Path(a.out), a.keep_days, a.keep_min)


if __name__ == "__main__":
    main()
