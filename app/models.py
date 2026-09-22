import re
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator

_SAFE_TEST_PATH_RE = re.compile(r"^tests?/.*test_[\w\-/]+\.py$")
_DEFAULT_TEST_PATH = "tests/test_ai_generated_regression.py"


class AnalyzeRequest(BaseModel):
    repo_url: HttpUrl
    ref: str = Field(description="Branch name, commit SHA, PR number, or PR URL")
    error_description: str | None = Field(
        default=None, description="Traceback or error description, if already known"
    )
    test_command: str | None = Field(
        default=None, description="Optional pytest node id or path to run instead of the whole suite"
    )


class ResolvedRef(BaseModel):
    sha: str
    ref_kind: Literal["branch", "sha", "pr"]
    pr_number: int | None = None
    clone_url: str


class TracebackFrame(BaseModel):
    file: str
    line: int
    function: str


class CodeContext(BaseModel):
    file: str
    start_line: int
    end_line: int
    source: str


class DiscoveredFailure(BaseModel):
    test_nodeid: str | None = None
    traceback_text: str
    frames: list[TracebackFrame] = []


class DiagnosisInfo(BaseModel):
    summary: str
    root_cause: str
    confidence: Literal["high", "medium", "low"]
    affected_files: list[str] = []
    notes: str | None = None


class RegressionTest(BaseModel):
    path: str
    code: str


class LLMAnalysisPayload(BaseModel):
    diagnosis_summary: str
    root_cause: str
    confidence: Literal["high", "medium", "low"]
    affected_files: list[str] = []
    patch: str
    regression_test_path: str
    regression_test_code: str
    notes: str | None = None

    @field_validator("regression_test_path")
    @classmethod
    def _sanitize_test_path(cls, value: str) -> str:
        # First line of defense against a path-traversal-shaped test path from the LLM;
        # app.sandbox re-checks the resolved path stays inside the repo regardless.
        if not _SAFE_TEST_PATH_RE.match(value):
            return _DEFAULT_TEST_PATH
        return value


class ValidationResult(BaseModel):
    baseline_outcome: Literal["fail", "pass", "error", "timeout"]
    baseline_output: str
    patch_applied: bool
    patched_outcome: Literal["fail", "pass", "error", "timeout", "not_run"]
    patched_output: str
    validated: bool


class AnalyzeResponse(BaseModel):
    status: Literal["success", "partial", "failed"]
    error_source: Literal["user_provided", "auto_discovered", "none"] = "none"
    resolved_ref: ResolvedRef | None = None
    diagnosis: DiagnosisInfo | None = None
    patch: str | None = None
    regression_test: RegressionTest | None = None
    validation: ValidationResult | None = None
    warnings: list[str] = []
    error_message: str | None = None
