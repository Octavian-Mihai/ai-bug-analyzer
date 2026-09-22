from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config import Settings
from app.models import ValidationResult
from app.subprocess_utils import run_subprocess

_OUTPUT_TRUNCATE_CHARS = 8000

PytestOutcome = Literal["pass", "fail", "error", "timeout"]


@dataclass
class PytestRunResult:
    outcome: PytestOutcome
    output: str


class SandboxError(Exception):
    pass


@contextmanager
def temp_clone_workspace(prefix: str, root: Path | None = None) -> Iterator[Path]:
    """Creates a temp directory and guarantees its removal, even on unexpected errors."""
    workspace = Path(tempfile.mkdtemp(prefix=prefix, dir=str(root) if root else None))
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _classify_pytest_outcome(returncode: int | None, timed_out: bool) -> PytestOutcome:
    if timed_out:
        return "timeout"
    if returncode == 0:
        return "pass"
    if returncode == 1:
        return "fail"
    # 2/3/4/5 = usage error, internal error, interrupted, no tests collected
    return "error"


def run_pytest_subprocess(
    repo_dir: Path, nodeid_or_path: str, timeout: int, memory_limit_mb: int
) -> PytestRunResult:
    result = run_subprocess(
        ["pytest", "--tb=short", "-q", nodeid_or_path],
        cwd=repo_dir,
        timeout=timeout,
        memory_limit_mb=memory_limit_mb,
    )
    outcome = _classify_pytest_outcome(result.returncode, result.timed_out)
    return PytestRunResult(outcome=outcome, output=result.stdout[-_OUTPUT_TRUNCATE_CHARS:])


def _resolve_safe_path(repo_dir: Path, relative_path: str) -> Path:
    """Rejects a test path that would escape the repo dir (second guard past app.llm's regex check)."""
    candidate = (repo_dir / relative_path).resolve()
    if not candidate.is_relative_to(repo_dir.resolve()):
        raise SandboxError(f"Regression test path escapes the repository: {relative_path}")
    return candidate


def run_validation(
    repo_dir: Path,
    patch_text: str,
    test_path: str,
    test_code: str,
    settings: Settings,
) -> ValidationResult:
    """Runs the red -> green validation cycle against a disposable repo_dir clone.

    1. Write the new regression test into the UNPATCHED repo and run it: expect "fail",
       proving the test reproduces the bug.
    2. `git apply` the proposed patch.
    3. Re-run the same test: expect "pass", proving the fix works.
    repo_dir is assumed to already be a throwaway clone the caller will discard.
    """
    repo_root = repo_dir.resolve()
    test_file = _resolve_safe_path(repo_root, test_path)
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(test_code, encoding="utf-8")
    relative_test_path = str(test_file.relative_to(repo_root))

    baseline = run_pytest_subprocess(
        repo_root, relative_test_path, settings.pytest_timeout_seconds, settings.pytest_memory_limit_mb
    )

    patch_file = repo_root / ".ai-bug-analyzer.patch"
    patch_file.write_text(patch_text, encoding="utf-8")
    try:
        check_result = run_subprocess(
            ["git", "apply", "--check", str(patch_file)], cwd=repo_root, timeout=settings.clone_timeout_seconds
        )
        if check_result.timed_out or check_result.returncode != 0:
            return ValidationResult(
                baseline_outcome=baseline.outcome,
                baseline_output=baseline.output,
                patch_applied=False,
                patched_outcome="not_run",
                patched_output=check_result.stdout[-_OUTPUT_TRUNCATE_CHARS:],
                validated=False,
            )

        apply_result = run_subprocess(
            ["git", "apply", str(patch_file)], cwd=repo_root, timeout=settings.clone_timeout_seconds
        )
        if apply_result.timed_out or apply_result.returncode != 0:
            return ValidationResult(
                baseline_outcome=baseline.outcome,
                baseline_output=baseline.output,
                patch_applied=False,
                patched_outcome="not_run",
                patched_output=apply_result.stdout[-_OUTPUT_TRUNCATE_CHARS:],
                validated=False,
            )
    finally:
        patch_file.unlink(missing_ok=True)

    patched = run_pytest_subprocess(
        repo_root, relative_test_path, settings.pytest_timeout_seconds, settings.pytest_memory_limit_mb
    )

    validated = baseline.outcome == "fail" and patched.outcome == "pass"
    return ValidationResult(
        baseline_outcome=baseline.outcome,
        baseline_output=baseline.output,
        patch_applied=True,
        patched_outcome=patched.outcome,
        patched_output=patched.output,
        validated=validated,
    )
