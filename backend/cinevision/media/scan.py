"""Optional ClamAV scanning through a local clamd UNIX socket (INSTREAM)."""
from __future__ import annotations

import socket
import struct
from pathlib import Path


def scan_file(path: Path, clamd_socket: str | None) -> str:
    """Returns 'clean', 'infected' or 'not_configured'. Raises on scanner errors."""
    if not clamd_socket:
        return "not_configured"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(120)
        s.connect(clamd_socket)
        s.sendall(b"zINSTREAM\0")
        with open(path, "rb") as f:
            while chunk := f.read(1 << 16):
                s.sendall(struct.pack(">I", len(chunk)) + chunk)
        s.sendall(struct.pack(">I", 0))
        reply = s.recv(4096).decode(errors="replace")
    if reply.rstrip("\0").endswith("OK"):
        return "clean"
    if "FOUND" in reply:
        return "infected"
    raise RuntimeError("malware scanner returned an unexpected response")
