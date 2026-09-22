import subprocess

import httpx
import pytest
import respx

from app.github_client import (
    GithubClientError,
    GithubNotFoundError,
    GithubRateLimitError,
    check_repo_size,
    classify_ref,
    clone_repo,
    parse_repo_url,
    resolve_ref,
    validate_host,
)
from app.models import ResolvedRef


def test_parse_repo_url_basic():
    assert parse_repo_url("https://github.com/octa/demo") == ("octa", "demo")


def test_parse_repo_url_strips_git_suffix():
    assert parse_repo_url("https://github.com/octa/demo.git") == ("octa", "demo")


def test_parse_repo_url_missing_repo_raises():
    with pytest.raises(GithubClientError):
        parse_repo_url("https://github.com/octa")


def test_validate_host_allows_listed_host():
    validate_host("https://github.com/octa/demo", ["github.com"])


def test_validate_host_rejects_other_host():
    with pytest.raises(GithubClientError):
        validate_host("https://evil.example.com/octa/demo", ["github.com"])


@pytest.mark.parametrize(
    "ref,expected",
    [
        ("main", "branch"),
        ("feature/foo", "branch"),
        ("42", "pr_number"),
        ("#42", "pr_number"),
        ("https://github.com/o/r/pull/42", "pr_url"),
        ("a1b2c3d", "sha"),
        ("a" * 40, "sha"),
    ],
)
def test_classify_ref(ref, expected):
    assert classify_ref(ref) == expected


@respx.mock
def test_resolve_ref_pr_number_uses_head_sha_and_fork_clone_url():
    respx.get("https://api.github.com/repos/octa/demo/pulls/42").mock(
        return_value=httpx.Response(
            200,
            json={"head": {"sha": "deadbeef", "repo": {"clone_url": "https://github.com/forker/demo.git"}}},
        )
    )
    with httpx.Client() as client:
        resolved = resolve_ref("octa", "demo", "42", None, client)

    assert resolved == ResolvedRef(
        sha="deadbeef", ref_kind="pr", pr_number=42, clone_url="https://github.com/forker/demo.git"
    )


@respx.mock
def test_resolve_ref_bare_number_falls_back_to_branch_on_404():
    respx.get("https://api.github.com/repos/octa/demo/pulls/42").mock(return_value=httpx.Response(404))
    respx.get("https://api.github.com/repos/octa/demo/branches/42").mock(
        return_value=httpx.Response(200, json={"commit": {"sha": "branchsha"}})
    )
    with httpx.Client() as client:
        resolved = resolve_ref("octa", "demo", "42", None, client)

    assert resolved.ref_kind == "branch"
    assert resolved.sha == "branchsha"


@respx.mock
def test_resolve_ref_pr_url_404_raises():
    respx.get("https://api.github.com/repos/o/r/pulls/42").mock(return_value=httpx.Response(404))
    with httpx.Client() as client:
        with pytest.raises(GithubNotFoundError):
            resolve_ref("o", "r", "https://github.com/o/r/pull/42", None, client)


@respx.mock
def test_resolve_ref_branch():
    respx.get("https://api.github.com/repos/octa/demo/branches/main").mock(
        return_value=httpx.Response(200, json={"commit": {"sha": "mainsha"}})
    )
    with httpx.Client() as client:
        resolved = resolve_ref("octa", "demo", "main", None, client)

    assert resolved == ResolvedRef(sha="mainsha", ref_kind="branch", clone_url="https://github.com/octa/demo.git")


def test_resolve_ref_sha_makes_no_api_call():
    with httpx.Client() as client:
        resolved = resolve_ref("octa", "demo", "a1b2c3d", None, client)

    assert resolved.ref_kind == "sha"
    assert resolved.sha == "a1b2c3d"


@respx.mock
def test_check_repo_size_raises_when_too_large():
    respx.get("https://api.github.com/repos/octa/demo").mock(
        return_value=httpx.Response(200, json={"size": 999_999})
    )
    with httpx.Client() as client:
        with pytest.raises(GithubClientError):
            check_repo_size("octa", "demo", None, client, max_repo_size_mb=1)


@respx.mock
def test_check_repo_size_forbidden_raises_rate_limit_error():
    respx.get("https://api.github.com/repos/octa/demo").mock(return_value=httpx.Response(403))
    with httpx.Client() as client:
        with pytest.raises(GithubRateLimitError):
            check_repo_size("octa", "demo", None, client, max_repo_size_mb=500)


def test_clone_repo_branch(tmp_path, buggy_repo_remote):
    resolved = ResolvedRef(sha="", ref_kind="branch", clone_url=buggy_repo_remote)
    dest = tmp_path / "cloned"

    clone_repo(buggy_repo_remote, resolved, "main", dest, timeout=30)

    assert (dest / "src" / "buggy_pkg" / "calc.py").is_file()


def test_clone_repo_sha_checks_out_exact_commit(tmp_path, buggy_repo_remote):
    probe_dest = tmp_path / "probe"
    probe_resolved = ResolvedRef(sha="", ref_kind="branch", clone_url=buggy_repo_remote)
    clone_repo(buggy_repo_remote, probe_resolved, "main", probe_dest, timeout=30)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=probe_dest, capture_output=True, text=True, check=True
    ).stdout.strip()

    dest = tmp_path / "sha_clone"
    resolved = ResolvedRef(sha=head_sha, ref_kind="sha", clone_url=buggy_repo_remote)
    clone_repo(buggy_repo_remote, resolved, head_sha, dest, timeout=30)

    checked_out_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=dest, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert checked_out_sha == head_sha


def test_clone_repo_missing_branch_raises(tmp_path, buggy_repo_remote):
    resolved = ResolvedRef(sha="", ref_kind="branch", clone_url=buggy_repo_remote)
    with pytest.raises(GithubClientError):
        clone_repo(buggy_repo_remote, resolved, "does-not-exist", tmp_path / "cloned", timeout=30)
