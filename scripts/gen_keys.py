#!/usr/bin/env python3
"""Generate the master key and the (separate) backup key as files readable only by the current user.

    python scripts/gen_keys.py deploy/secrets

Keep these files OUTSIDE the data volume. Losing the master key makes all stored footage unrecoverable;
losing the backup key makes backups unrecoverable. Store a copy of each somewhere safe and offline.
"""
import base64
import os
import sys
from pathlib import Path

out = Path(sys.argv[1] if len(sys.argv) > 1 else "deploy/secrets")
out.mkdir(parents=True, exist_ok=True)
os.chmod(out, 0o700)
for name in ("master_key", "backup_key"):
    p = out / name
    if p.exists():
        print(f"{p} already exists — not overwriting")
        continue
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(base64.b64encode(os.urandom(32)).decode() + "\n")
    print(f"wrote {p}")
