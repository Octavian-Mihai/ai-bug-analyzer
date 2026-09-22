from __future__ import annotations

from pathlib import Path

import httpx

from app.config import Settings
from app.context import extract_code_context
from app.error_capture import discover_failing_test, parse_user_traceback
from app.github_client import (
    GithubClientError,
    check_repo_size,
    clone_repo,
    parse_repo_url,
    resolve_ref,
    validate_host,
)
from app.llm import LLMClient, LLMResponseError
from app.models import AnalyzeRequest, AnalyzeResponse, DiagnosisInfo, RegressionTest
from app.sandbox import SandboxError, run_validation, temp_clone_workspace


def analyze(request: AnalyzeRequest, settings: Settings, llm_client: LLMClient) -> AnalyzeResponse:
    warnings: list[str] = []

    try:
        validate_host(str(request.repo_url), settings.allowed_git_hosts)
        owner, repo = parse_repo_url(str(request.repo_url))
    except GithubClientError as exc:
        return AnalyzeResponse(status="failed", error_message=str(exc))

    with httpx.Client(timeout=30) as http_client:
        try:
            check_repo_size(owner, repo, settings.github_token, http_client, settings.max_repo_size_mb)
            resolved = resolve_ref(owner, repo, request.ref, settings.github_token, http_client)
        except GithubClientError as exc:
            return AnalyzeResponse(status="failed", error_message=str(exc))

    workdir_root = Path(settings.workdir_root) if settings.workdir_root else None
    with temp_clone_workspace(prefix="ai-bug-analyzer-", root=workdir_root) as workspace:
        repo_dir = workspace / "repo"
        try:
            clone_repo(resolved.clone_url, resolved, request.ref, repo_dir, settings.clone_timeout_seconds)
        except GithubClientError as exc:
            return AnalyzeResponse(status="failed", resolved_ref=resolved, error_message=str(exc))

        if request.error_description:
            error_source = "user_provided"
            traceback_text = request.error_description
            frames = parse_user_traceback(request.error_description)
        else:
            discovered = discover_failing_test(repo_dir, request.test_command, settings)
            if discovered is None:
                return AnalyzeResponse(
                    status="failed",
                    resolved_ref=resolved,
                    error_message=(
                        "No failing test was found and no error_description was provided. "
                        "Pass error_description to describe the bug explicitly."
                    ),
                )
            error_source = "auto_discovered"
            traceback_text = discovered.traceback_text
            frames = discovered.frames

        context = extract_code_context(repo_dir, frames, settings)
        if not context:
            warnings.append(
                "Could not locate any source files referenced by the traceback in the cloned repo."
            )

        try:
            analysis = llm_client.analyze_bug(
                context=context, traceback_text=traceback_text, error_description=request.error_description
            )
        except LLMResponseError as exc:
            return AnalyzeResponse(
                status="failed",
                resolved_ref=resolved,
                error_source=error_source,
                error_message=str(exc),
                warnings=warnings,
            )

        diagnosis = DiagnosisInfo(
            summary=analysis.diagnosis_summary,
            root_cause=analysis.root_cause,
            confidence=analysis.confidence,
            affected_files=analysis.affected_files,
            notes=analysis.notes,
        )
        regression_test = RegressionTest(
            path=analysis.regression_test_path, code=analysis.regression_test_code
        )

        try:
            validation = run_validation(
                repo_dir,
                analysis.patch,
                analysis.regression_test_path,
                analysis.regression_test_code,
                settings,
            )
        except SandboxError as exc:
            return AnalyzeResponse(
                status="partial",
                resolved_ref=resolved,
                error_source=error_source,
                diagnosis=diagnosis,
                patch=analysis.patch,
                regression_test=regression_test,
                warnings=warnings + [str(exc)],
            )

        return AnalyzeResponse(
            status="success" if validation.validated else "partial",
            resolved_ref=resolved,
            error_source=error_source,
            diagnosis=diagnosis,
            patch=analysis.patch,
            regression_test=regression_test,
            validation=validation,
            warnings=warnings,
        )
