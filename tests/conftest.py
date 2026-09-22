from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _run_git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _build_bare_remote(tmp_path: Path, name: str, extra_files: dict[str, str] | None = None) -> str:
    """Builds a local bare git remote from tests/fixtures/buggy_repo and returns its file:// URL.

    Using a local bare remote (instead of real github.com) keeps clone/checkout tests
    fully offline and deterministic. extra_files lets a caller add files (e.g. a test
    that already exercises the fixture's deliberate bug) before the initial commit.
    """
    work_copy = tmp_path / f"{name}_work_copy"
    shutil.copytree(FIXTURES_DIR / "buggy_repo", work_copy)

    for relative_path, content in (extra_files or {}).items():
        target = work_copy / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    _run_git(["init", "-q", "-b", "main"], work_copy)
    _run_git(["config", "user.email", "test@example.com"], work_copy)
    _run_git(["config", "user.name", "Test"], work_copy)
    _run_git(["add", "."], work_copy)
    _run_git(["commit", "-q", "-m", "initial"], work_copy)

    bare_remote = tmp_path / f"{name}.git"
    _run_git(["init", "-q", "--bare", "-b", "main", str(bare_remote)], tmp_path)
    _run_git(["push", "-q", str(bare_remote), "main"], work_copy)

    return f"file://{bare_remote}"


@pytest.fixture
def buggy_repo_remote(tmp_path: Path) -> str:
    """A bare remote whose committed test suite is currently green (doesn't cover the bug)."""
    return _build_bare_remote(tmp_path, "remote")


@pytest.fixture
def buggy_repo_with_failing_test_remote(tmp_path: Path) -> str:
    """A bare remote that already has a committed test triggering the fixture's deliberate bug."""
    return _build_bare_remote(
        tmp_path,
        "remote_failing",
        extra_files={
            "tests/test_trigger_bug.py": (
                "from buggy_pkg.calc import sum_first_n\n\n"
                "def test_trigger():\n"
                "    sum_first_n([1, 2, 3], 3)\n"
            )
        },
    )


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        anthropic_api_key="test-key-not-real",
        github_token=None,
        clone_timeout_seconds=30,
        max_repo_size_mb=500,
        pip_install_timeout_seconds=10,
        pytest_discovery_timeout_seconds=30,
        pytest_timeout_seconds=30,
        pytest_memory_limit_mb=512,
        workdir_root=str(tmp_path),
    )


class FakeToolUseBlock:
    type = "tool_use"

    def __init__(self, block_id: str, name: str, input: dict[str, Any]):
        self.id = block_id
        self.name = name
        self.input = input


class FakeMessage:
    def __init__(self, content: list[Any]):
        self.content = content


class FakeMessagesResource:
    """Stands in for anthropic.Anthropic().messages, returning queued canned responses."""

    def __init__(self, responses: list[FakeMessage]):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> FakeMessage:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeMessagesResource ran out of queued responses")
        return self._responses.pop(0)


class FakeAnthropicClient:
    def __init__(self, responses: list[FakeMessage]):
        self.messages = FakeMessagesResource(responses)


def make_tool_use_message(tool_name: str, payload: dict[str, Any], block_id: str = "tool_1") -> FakeMessage:
    return FakeMessage([FakeToolUseBlock(block_id, tool_name, payload)])
