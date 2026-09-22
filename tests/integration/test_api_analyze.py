from fastapi.testclient import TestClient

from app.config import get_settings
from app.llm import TOOL_NAME, LLMClient
from app.main import app, get_llm_client
from app.models import ResolvedRef
from tests.conftest import FakeAnthropicClient, make_tool_use_message

ANALYSIS_PAYLOAD = {
    "diagnosis_summary": "Off-by-one bug in sum_first_n",
    "root_cause": "range(n + 1) reads one element past the intended window",
    "confidence": "high",
    "affected_files": ["src/buggy_pkg/calc.py"],
    "patch": "\n".join(
        [
            "--- a/src/buggy_pkg/calc.py",
            "+++ b/src/buggy_pkg/calc.py",
            "@@ -7,5 +7,5 @@",
            '     """',
            "     total = 0",
            "-    for i in range(n + 1):",
            "+    for i in range(n):",
            "         total += numbers[i]",
            "     return total",
            "",
        ]
    ),
    "regression_test_path": "tests/test_ai_regression.py",
    "regression_test_code": (
        "from buggy_pkg.calc import sum_first_n\n\n\n"
        "def test_sum_first_n_full_length():\n"
        "    assert sum_first_n([1, 2, 3], 3) == 6\n"
    ),
    "notes": None,
}


def _install_overrides(test_settings, fake_llm) -> None:
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_llm_client] = lambda: fake_llm


def test_analyze_user_provided_error_end_to_end_success(test_settings, buggy_repo_remote, monkeypatch):
    # No real GitHub API calls: host validation runs for real (pure URL parsing), but the
    # network-touching resolve/size-check calls are swapped for a local file:// clone.
    monkeypatch.setattr("app.orchestrator.check_repo_size", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.orchestrator.resolve_ref",
        lambda *a, **k: ResolvedRef(sha="", ref_kind="branch", clone_url=buggy_repo_remote),
    )

    fake_llm = LLMClient(
        api_key="x", model="claude-x", client=FakeAnthropicClient([make_tool_use_message(TOOL_NAME, ANALYSIS_PAYLOAD)])
    )
    _install_overrides(test_settings, fake_llm)
    try:
        response = TestClient(app).post(
            "/analyze",
            json={
                "repo_url": "https://github.com/testorg/testrepo",
                "ref": "main",
                "error_description": "IndexError: list index out of range in sum_first_n",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["error_source"] == "user_provided"
    assert body["validation"]["validated"] is True
    assert body["patch"]


def test_analyze_auto_discovered_error_end_to_end_success(
    test_settings, buggy_repo_with_failing_test_remote, monkeypatch
):
    monkeypatch.setattr("app.orchestrator.check_repo_size", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.orchestrator.resolve_ref",
        lambda *a, **k: ResolvedRef(sha="", ref_kind="branch", clone_url=buggy_repo_with_failing_test_remote),
    )

    fake_llm = LLMClient(
        api_key="x", model="claude-x", client=FakeAnthropicClient([make_tool_use_message(TOOL_NAME, ANALYSIS_PAYLOAD)])
    )
    _install_overrides(test_settings, fake_llm)
    try:
        response = TestClient(app).post(
            "/analyze", json={"repo_url": "https://github.com/testorg/testrepo", "ref": "main"}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["error_source"] == "auto_discovered"
    assert body["validation"]["validated"] is True


def test_analyze_returns_failed_when_no_bug_found(test_settings, buggy_repo_remote, monkeypatch):
    monkeypatch.setattr("app.orchestrator.check_repo_size", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.orchestrator.resolve_ref",
        lambda *a, **k: ResolvedRef(sha="", ref_kind="branch", clone_url=buggy_repo_remote),
    )

    fake_llm = LLMClient(api_key="x", model="claude-x", client=FakeAnthropicClient([]))
    _install_overrides(test_settings, fake_llm)
    try:
        response = TestClient(app).post(
            "/analyze", json={"repo_url": "https://github.com/testorg/testrepo", "ref": "main"}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error_message"]


def test_analyze_rejects_disallowed_host(test_settings):
    _install_overrides(test_settings, LLMClient(api_key="x", model="claude-x", client=FakeAnthropicClient([])))
    try:
        response = TestClient(app).post(
            "/analyze", json={"repo_url": "https://evil.example.com/testorg/testrepo", "ref": "main"}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert "not in the allowed list" in body["error_message"]
