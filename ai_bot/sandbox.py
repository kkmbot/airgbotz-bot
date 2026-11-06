"""Admin-only Python runner. NOT a hard security boundary: it removes secrets from the environment,
limits CPU/memory/time/output and runs in a throwaway folder. Off unless ENABLE_ADMIN_SANDBOX=true."""
from __future__ import annotations

import asyncio
import os
import resource
import shutil
import sys
import tempfile

TIMEOUT = 10
MAX_OUT = 3000


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (5 * 1024 * 1024, 5 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    os.setsid()


def harden_parent() -> None:
    """Make this process non-dumpable so a child of the same user cannot read its /proc environ or memory."""
    try:
        import ctypes
        ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE = 4
    except Exception:  # noqa: BLE001
        pass


async def run_python(code: str, timeout: int = TIMEOUT) -> str:
    work = tempfile.mkdtemp(prefix="sbx_")
    try:
        path = os.path.join(work, "main.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        env = {"PATH": "/usr/bin:/bin", "HOME": work, "PYTHONIOENCODING": "utf-8", "LANG": "C.UTF-8"}
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", path, cwd=work, env=env, preexec_fn=_limits,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            note = "" if proc.returncode == 0 else f"\n[exit code {proc.returncode}]"
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, 9)
            except ProcessLookupError:
                pass
            out, note = b"", f"[stopped after {timeout}s]"
        text = out.decode("utf-8", errors="replace")
        if len(text) > MAX_OUT:
            text = text[:MAX_OUT] + "\n[output cut]"
        return (text + note).strip() or "(no output)"
    finally:
        shutil.rmtree(work, ignore_errors=True)
