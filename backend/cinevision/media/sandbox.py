"""Run media tools on untrusted input with hard resource limits.

Defence in depth (in addition to container isolation in deploy/):
* fresh session, empty environment, no stdin
* RLIMIT_CPU / RLIMIT_AS / RLIMIT_FSIZE / RLIMIT_NOFILE, no core dumps
* wall-clock timeout with process-group kill
* callers pass `-protocol_whitelist file` so container tricks can't reach the network
"""
from __future__ import annotations

import os
import resource
import signal
import subprocess
from dataclasses import dataclass


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


def run(cmd: list[str], limits: Limits | None = None, cwd: str | None = None) -> subprocess.CompletedProcess:
    lim = limits or Limits()
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"}
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, cwd=cwd, preexec_fn=_preexec(lim), close_fds=True)
    try:
        out, err = proc.communicate(timeout=lim.timeout_s)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise SandboxError("media tool timed out")
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
