import pytest

from app.llm import TOOL_NAME, LLMClient, LLMResponseError
from app.models import CodeContext
from tests.conftest import FakeAnthropicClient, make_tool_use_message

VALID_PAYLOAD = {
    "diagnosis_summary": "Off-by-one in sum_first_n",
    "root_cause": "range(n + 1) reads one element too many",
    "confidence": "high",
    "affected_files": ["src/buggy_pkg/calc.py"],
    "patch": (
        "--- a/src/buggy_pkg/calc.py\n+++ b/src/buggy_pkg/calc.py\n"
        "@@\n-    for i in range(n + 1):\n+    for i in range(n):\n"
    ),
    "regression_test_path": "tests/test_bug_fix.py",
    "regression_test_code": "def test_x():\n    assert True\n",
    "notes": None,
}


def test_analyze_bug_returns_parsed_payload_on_first_success():
    fake_client = FakeAnthropicClient([make_tool_use_message(TOOL_NAME, VALID_PAYLOAD)])
    llm = LLMClient(api_key="x", model="claude-x", client=fake_client)

    result = llm.analyze_bug(context=[], traceback_text="IndexError", error_description=None)

    assert result.diagnosis_summary == VALID_PAYLOAD["diagnosis_summary"]
    assert len(fake_client.messages.calls) == 1


def test_analyze_bug_retries_once_after_invalid_payload_then_succeeds():
    invalid_payload = dict(VALID_PAYLOAD, confidence="extremely-high")

    fake_client = FakeAnthropicClient(
        [
            make_tool_use_message(TOOL_NAME, invalid_payload, block_id="tool_1"),
            make_tool_use_message(TOOL_NAME, VALID_PAYLOAD, block_id="tool_2"),
        ]
    )
    llm = LLMClient(api_key="x", model="claude-x", max_retries=1, client=fake_client)

    result = llm.analyze_bug(context=[], traceback_text="IndexError", error_description=None)

    assert result.confidence == "high"
    assert len(fake_client.messages.calls) == 2
    second_call_messages = fake_client.messages.calls[1]["messages"]
    assert second_call_messages[-1]["content"][0]["type"] == "tool_result"


def test_analyze_bug_raises_after_exhausting_retries():
    invalid_payload = dict(VALID_PAYLOAD, confidence="nope")
    fake_client = FakeAnthropicClient(
        [
            make_tool_use_message(TOOL_NAME, invalid_payload, block_id="tool_1"),
            make_tool_use_message(TOOL_NAME, invalid_payload, block_id="tool_2"),
        ]
    )
    llm = LLMClient(api_key="x", model="claude-x", max_retries=1, client=fake_client)

    with pytest.raises(LLMResponseError):
        llm.analyze_bug(context=[], traceback_text="IndexError", error_description=None)


def test_analyze_bug_sanitizes_unsafe_regression_test_path():
    unsafe_payload = dict(VALID_PAYLOAD, regression_test_path="../../etc/passwd")
    fake_client = FakeAnthropicClient([make_tool_use_message(TOOL_NAME, unsafe_payload)])
    llm = LLMClient(api_key="x", model="claude-x", client=fake_client)

    result = llm.analyze_bug(context=[], traceback_text="x", error_description=None)

    assert result.regression_test_path == "tests/test_ai_generated_regression.py"


def test_build_prompt_includes_context_and_traceback():
    ctx = [CodeContext(file="a.py", start_line=1, end_line=2, source="x = 1")]

    prompt = LLMClient._build_prompt(ctx, "Traceback text", "user description")

    assert "user description" in prompt
    assert "Traceback text" in prompt
    assert "a.py" in prompt
    assert "x = 1" in prompt
