from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Environment variables that could leak credentials or proxy config into a
# subprocess that runs code/config from a cloned (untrusted) repository.
_STRIPPED_ENV_VARS = {
    "ANTHROPIC_API_KEY",
    "GITHUB_TOKEN",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
}


@dataclass
class SubprocessResult:
    returncode: int | None
    stdout: str
    timed_out: bool


def _safe_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV_VARS}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _limit_resources(memory_limit_mb: int | None, cpu_seconds: int | None):
    """Returns a preexec_fn applying POSIX rlimits, or None on non-POSIX platforms."""
    if sys.platform == "win32" or (memory_limit_mb is None and cpu_seconds is None):
        return None

    def _set_limits() -> None:
        # Note: process-group/session setup is handled by Popen(start_new_session=True)
        # in run_subprocess, not here - calling os.setsid() again would fail since the
        # child is already a session leader by the time preexec_fn runs.
        import resource

        if memory_limit_mb is not None:
            limit_bytes = memory_limit_mb * 1024 * 1024
            try:
                resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
            except (ValueError, OSError):
                pass  # e.g. macOS often rejects RLIMIT_AS; best-effort only
        if cpu_seconds is not None:
            try:
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            except (ValueError, OSError):
                pass

    return _set_limits


def run_subprocess(
    args: list[str],
    cwd: Path,
    timeout: int,
    memory_limit_mb: int | None = None,
    cpu_seconds: int | None = None,
) -> SubprocessResult:
    """Run a subprocess in its own process group, killing the whole group on timeout.

    Using ``subprocess.run(..., timeout=...)`` only terminates the direct child; a
    child that spawns its own workers (e.g. pytest with xdist) can leave orphans
    running past the timeout. Starting a new session lets us kill the whole group.
    """
    preexec_fn = None if sys.platform == "win32" else _limit_resources(memory_limit_mb, cpu_seconds)

    proc = subprocess.Popen(
        args,
        cwd=str(cwd),
        env=_safe_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=(sys.platform != "win32"),
        preexec_fn=preexec_fn,
    )

    try:
        stdout, _ = proc.communicate(timeout=timeout)
        return SubprocessResult(returncode=proc.returncode, stdout=stdout, timed_out=False)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        stdout, _ = proc.communicate()
        return SubprocessResult(returncode=None, stdout=stdout, timed_out=True)


def _kill_process_group(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        proc.kill()
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
