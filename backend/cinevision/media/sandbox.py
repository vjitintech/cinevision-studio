"""Run media tools on untrusted input with hard resource limits.

Defence in depth (in addition to container isolation in deploy/):
* fresh session, minimal environment, no stdin
* Linux/macOS: RLIMIT_CPU / RLIMIT_AS / RLIMIT_FSIZE / RLIMIT_NOFILE, no core dumps, process-group kill
* Windows (native, no Docker): no rlimits are available, so only the wall-clock timeout and a minimal
  environment apply — run under Docker Desktop when processing footage you don't trust
* callers pass `-protocol_whitelist file` so container tricks can't reach the network
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass

WINDOWS = sys.platform == "win32"
if not WINDOWS:
    import resource


class SandboxError(Exception):
    pass


@dataclass
class Limits:
    cpu_s: int = 600
    mem_bytes: int = 3 * 1024 ** 3
    fsize_bytes: int = 4 * 1024 ** 3
    nofile: int = 256
    timeout_s: int = 600


def _preexec(lim: Limits):
    def fn():
        os.setsid()
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CPU, (lim.cpu_s, lim.cpu_s + 5))
        resource.setrlimit(resource.RLIMIT_AS, (lim.mem_bytes, lim.mem_bytes))
        resource.setrlimit(resource.RLIMIT_FSIZE, (lim.fsize_bytes, lim.fsize_bytes))
        resource.setrlimit(resource.RLIMIT_NOFILE, (lim.nofile, lim.nofile))
    return fn


def _env() -> dict:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"}
    if WINDOWS:  # Windows programs need these to start at all
        for k in ("SYSTEMROOT", "SystemRoot", "WINDIR", "TEMP", "TMP", "PATHEXT"):
            if k in os.environ:
                env[k] = os.environ[k]
    return env


def run(cmd: list[str], limits: Limits | None = None, cwd: str | None = None) -> subprocess.CompletedProcess:
    lim = limits or Limits()
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "env": _env(), "cwd": cwd}
    if WINDOWS:
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kw.update(preexec_fn=_preexec(lim), close_fds=True)
    proc = subprocess.Popen(cmd, **kw)
    try:
        out, err = proc.communicate(timeout=lim.timeout_s)
    except subprocess.TimeoutExpired:
        try:
            if WINDOWS:
                proc.kill()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise SandboxError("media tool timed out")
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
