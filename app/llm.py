from __future__ import annotations

from typing import Any

import anthropic
from pydantic import ValidationError

from app.models import CodeContext, LLMAnalysisPayload

TOOL_NAME = "submit_bug_analysis"

_TOOL_SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Submit a bug diagnosis, a proposed fix as a unified diff, and a regression "
        "test that reproduces the bug."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "diagnosis_summary": {
                "type": "string",
                "description": "One or two sentence summary of the bug.",
            },
            "root_cause": {"type": "string", "description": "Explanation of why the bug happens."},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "affected_files": {"type": "array", "items": {"type": "string"}},
            "patch": {
                "type": "string",
                "description": (
                    "A minimal unified diff (git apply compatible, with a/ b/ path "
                    "prefixes) fixing the bug, scoped to the files shown in context."
                ),
            },
            "regression_test_path": {
                "type": "string",
                "description": "Repo-relative path for the new test file, e.g. tests/test_bug_fix.py",
            },
            "regression_test_code": {
                "type": "string",
                "description": (
                    "A pytest test that fails against the unpatched code and passes "
                    "once the patch is applied."
                ),
            },
            "notes": {"type": "string", "description": "Any caveats or uncertainty."},
        },
        "required": [
            "diagnosis_summary", "root_cause", "confidence", "patch",
            "regression_test_path", "regression_test_code",
        ],
    },
}

_SYSTEM_PROMPT = (
    "You are an expert software debugger. Given a traceback and surrounding source "
    "code, diagnose the root cause, produce a minimal fix as a unified diff that "
    "applies cleanly with `git apply` against the shown files, and write a pytest "
    "regression test that fails on the current (unpatched) code and passes once the "
    "diff is applied. Only reference files that were shown to you in the context; "
    "never invent file paths. If you are uncertain, say so in `notes` and lower your "
    "confidence rather than guessing."
)


class LLMResponseError(Exception):
    def __init__(self, message: str, raw_payload: str = ""):
        super().__init__(message)
        self.raw_payload = raw_payload


class LLMClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int = 4096,
        max_retries: int = 1,
        client: anthropic.Anthropic | None = None,
    ):
        self._model = model
        self._max_tokens = max_tokens
        self._max_retries = max_retries
        self._client = client or anthropic.Anthropic(api_key=api_key)

    def analyze_bug(
        self,
        *,
        context: list[CodeContext],
        traceback_text: str,
        error_description: str | None,
    ) -> LLMAnalysisPayload:
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": self._build_prompt(context, traceback_text, error_description)}
        ]

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            message = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=_SYSTEM_PROMPT,
                tools=[_TOOL_SCHEMA],
                tool_choice={"type": "tool", "name": TOOL_NAME},
                messages=messages,
            )
            tool_use = self._find_tool_use(message)
            if tool_use is None:
                last_error = LLMResponseError("Model response contained no tool_use block")
                continue

            try:
                return LLMAnalysisPayload.model_validate(tool_use.input)
            except ValidationError as exc:
                last_error = exc
                messages.append({"role": "assistant", "content": message.content})
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use.id,
                                "content": f"Invalid input: {exc}. Call {TOOL_NAME} again with corrected fields.",
                                "is_error": True,
                            }
                        ],
                    }
                )

        raise LLMResponseError(
            f"LLM did not return a valid analysis after {self._max_retries + 1} attempt(s): {last_error}"
        )

    @staticmethod
    def _find_tool_use(message: Any):
        for block in message.content:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == TOOL_NAME:
                return block
        return None

    @staticmethod
    def _build_prompt(
        context: list[CodeContext], traceback_text: str, error_description: str | None
    ) -> str:
        parts: list[str] = []
        if error_description:
            parts.append(f"Error description:\n{error_description}\n")
        if traceback_text:
            parts.append(f"Traceback:\n{traceback_text}\n")
        parts.append("Relevant source code:\n")
        for ctx in context:
            parts.append(f"--- {ctx.file} (lines {ctx.start_line}-{ctx.end_line}) ---\n{ctx.source}\n")
        return "\n".join(parts)
