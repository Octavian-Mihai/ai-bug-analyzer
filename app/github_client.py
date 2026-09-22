from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.models import ResolvedRef
from app.subprocess_utils import run_subprocess

_PR_URL_RE = re.compile(r"/pull/(\d+)")
_PR_NUMBER_RE = re.compile(r"^#?(\d+)$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")

GITHUB_API_BASE = "https://api.github.com"


class GithubClientError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class GithubNotFoundError(GithubClientError):
    pass


class GithubRateLimitError(GithubClientError):
    pass


def parse_repo_url(repo_url: str) -> tuple[str, str]:
    parsed = urlparse(str(repo_url))
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        raise GithubClientError(f"Could not parse owner/repo from URL: {repo_url}")
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    return owner, repo


def validate_host(repo_url: str, allowed_hosts: list[str]) -> None:
    hostname = urlparse(str(repo_url)).hostname
    if hostname not in allowed_hosts:
        raise GithubClientError(f"Host '{hostname}' is not in the allowed list: {allowed_hosts}")


def classify_ref(ref: str) -> str:
    """Returns one of "pr_url", "pr_number", "sha", "branch"."""
    if _PR_URL_RE.search(ref):
        return "pr_url"
    if _PR_NUMBER_RE.match(ref):
        return "pr_number"
    if _SHA_RE.match(ref):
        return "sha"
    return "branch"


def _extract_pr_number(ref: str, kind: str) -> int:
    if kind == "pr_url":
        match = _PR_URL_RE.search(ref)
        assert match is not None
        return int(match.group(1))
    match = _PR_NUMBER_RE.match(ref)
    assert match is not None
    return int(match.group(1))


def _github_api_get(
    http_client: httpx.Client, url: str, github_token: str | None
) -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    response = http_client.get(url, headers=headers)
    if response.status_code == 404:
        raise GithubNotFoundError(f"Not found: {url}", status_code=404)
    if response.status_code == 403:
        raise GithubRateLimitError(
            f"GitHub API request forbidden or rate-limited: {url}", status_code=403
        )
    response.raise_for_status()
    return response.json()


def check_repo_size(
    owner: str,
    repo: str,
    github_token: str | None,
    http_client: httpx.Client,
    max_repo_size_mb: int,
    api_base: str = GITHUB_API_BASE,
) -> None:
    data = _github_api_get(http_client, f"{api_base}/repos/{owner}/{repo}", github_token)
    size_kb = data.get("size", 0)
    if size_kb > max_repo_size_mb * 1024:
        raise GithubClientError(
            f"Repository size ({size_kb} KB) exceeds the {max_repo_size_mb} MB limit"
        )


def resolve_ref(
    owner: str,
    repo: str,
    ref: str,
    github_token: str | None,
    http_client: httpx.Client,
    api_base: str = GITHUB_API_BASE,
) -> ResolvedRef:
    default_clone_url = f"https://github.com/{owner}/{repo}.git"
    kind = classify_ref(ref)

    if kind in ("pr_number", "pr_url"):
        pr_number = _extract_pr_number(ref, kind)
        try:
            data = _github_api_get(
                http_client, f"{api_base}/repos/{owner}/{repo}/pulls/{pr_number}", github_token
            )
            return ResolvedRef(
                sha=data["head"]["sha"],
                ref_kind="pr",
                pr_number=pr_number,
                clone_url=data["head"]["repo"]["clone_url"],
            )
        except GithubNotFoundError:
            if kind == "pr_url":
                raise
            # A bare number that isn't a real PR: fall through and try it as a branch name.
            ref = str(pr_number)
            kind = "branch"

    if kind == "sha":
        return ResolvedRef(sha=ref.lower(), ref_kind="sha", clone_url=default_clone_url)

    data = _github_api_get(
        http_client, f"{api_base}/repos/{owner}/{repo}/branches/{ref}", github_token
    )
    return ResolvedRef(sha=data["commit"]["sha"], ref_kind="branch", clone_url=default_clone_url)


def _raise_on_git_failure(result, action: str) -> None:
    if result.timed_out:
        raise GithubClientError(f"{action} timed out")
    if result.returncode != 0:
        raise GithubClientError(f"{action} failed: {result.stdout[-2000:]}")


def clone_repo(clone_url: str, resolved: ResolvedRef, ref: str, dest_dir: Path, timeout: int) -> None:
    dest_dir.parent.mkdir(parents=True, exist_ok=True)

    if resolved.ref_kind == "branch":
        args = [
            "git", "clone", "--branch", ref, "--single-branch", "--depth", "1",
            clone_url, str(dest_dir),
        ]
        result = run_subprocess(args, cwd=dest_dir.parent, timeout=timeout)
        _raise_on_git_failure(result, "git clone")
        return

    # PR and raw-SHA refs need full history reachability to check out an exact commit.
    clone_result = run_subprocess(
        ["git", "clone", clone_url, str(dest_dir)], cwd=dest_dir.parent, timeout=timeout
    )
    _raise_on_git_failure(clone_result, "git clone")

    checkout_result = run_subprocess(
        ["git", "checkout", resolved.sha], cwd=dest_dir, timeout=timeout
    )
    _raise_on_git_failure(checkout_result, "git checkout")
