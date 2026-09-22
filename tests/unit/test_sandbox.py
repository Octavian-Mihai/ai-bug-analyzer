import shutil

import pytest

from app.sandbox import SandboxError, _resolve_safe_path, run_pytest_subprocess, run_validation, temp_clone_workspace
from tests.conftest import FIXTURES_DIR, _run_git


def test_temp_clone_workspace_cleans_up_even_on_exception(tmp_path):
    captured_path = None
    with pytest.raises(RuntimeError):
        with temp_clone_workspace(prefix="test-", root=tmp_path) as workspace:
            captured_path = workspace
            assert workspace.exists()
            raise RuntimeError("boom")
    assert not captured_path.exists()


def test_run_pytest_subprocess_reports_timeout(tmp_path):
    (tmp_path / "test_slow.py").write_text("import time\n\ndef test_slow():\n    time.sleep(5)\n")

    result = run_pytest_subprocess(tmp_path, "test_slow.py", timeout=1, memory_limit_mb=256)

    assert result.outcome == "timeout"


def test_resolve_safe_path_rejects_traversal(tmp_path):
    with pytest.raises(SandboxError):
        _resolve_safe_path(tmp_path, "../outside.py")


def test_resolve_safe_path_allows_nested_path(tmp_path):
    resolved = _resolve_safe_path(tmp_path, "tests/test_foo.py")
    assert resolved == (tmp_path / "tests" / "test_foo.py").resolve()


def _init_git_repo(repo_dir) -> None:
    _run_git(["init", "-q", "-b", "main"], repo_dir)
    _run_git(["config", "user.email", "test@example.com"], repo_dir)
    _run_git(["config", "user.name", "Test"], repo_dir)
    _run_git(["add", "."], repo_dir)
    _run_git(["commit", "-q", "-m", "initial"], repo_dir)


def _copy_and_init_buggy_repo(tmp_path):
    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURES_DIR / "buggy_repo", repo_dir)
    _init_git_repo(repo_dir)
    return repo_dir


def _fix_patch_text() -> str:
    """Hand-written diff for the fixture's deliberate off-by-one bug (no LLM involved)."""
    context_prefix = " "
    lines = [
        "--- a/src/buggy_pkg/calc.py",
        "+++ b/src/buggy_pkg/calc.py",
        "@@ -7,5 +7,5 @@",
        context_prefix + '    """',
        context_prefix + "    total = 0",
        "-    for i in range(n + 1):",
        "+    for i in range(n):",
        context_prefix + "        total += numbers[i]",
        context_prefix + "    return total",
        "",
    ]
    return "\n".join(lines)


def test_run_validation_red_then_green(tmp_path, test_settings):
    repo_dir = _copy_and_init_buggy_repo(tmp_path)
    test_code = (
        "from buggy_pkg.calc import sum_first_n\n\n\n"
        "def test_sum_first_n_full_length():\n"
        "    assert sum_first_n([1, 2, 3], 3) == 6\n"
    )

    result = run_validation(repo_dir, _fix_patch_text(), "tests/test_regression.py", test_code, test_settings)

    assert result.baseline_outcome == "fail"
    assert result.patch_applied is True
    assert result.patched_outcome == "pass"
    assert result.validated is True


def test_run_validation_reports_unapplied_patch(tmp_path, test_settings):
    repo_dir = _copy_and_init_buggy_repo(tmp_path)
    test_code = "def test_noop():\n    assert True\n"

    result = run_validation(repo_dir, "this is not a valid diff\n", "tests/test_regression.py", test_code, test_settings)

    assert result.patch_applied is False
    assert result.patched_outcome == "not_run"
    assert result.validated is False


def test_run_validation_not_validated_when_baseline_already_passes(tmp_path, test_settings):
    repo_dir = _copy_and_init_buggy_repo(tmp_path)
    # A test that doesn't actually exercise the bug passes even before any patch.
    test_code = "def test_trivial():\n    assert True\n"

    result = run_validation(repo_dir, _fix_patch_text(), "tests/test_regression.py", test_code, test_settings)

    assert result.baseline_outcome == "pass"
    assert result.validated is False
