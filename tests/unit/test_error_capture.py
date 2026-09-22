import shutil
from pathlib import Path

from app.error_capture import discover_failing_test, parse_user_traceback

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

SAMPLE_TRACEBACK = """Traceback (most recent call last):
  File "/home/user/project/app.py", line 10, in main
    do_thing()
  File "/home/user/project/utils.py", line 5, in do_thing
    return 1 / 0
ZeroDivisionError: division by zero
"""

CHAINED_TRACEBACK = """Traceback (most recent call last):
  File "a.py", line 1, in first
    raise ValueError()
ValueError

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "b.py", line 2, in second
    raise RuntimeError()
RuntimeError
"""


def test_parse_user_traceback_extracts_frames_in_order():
    frames = parse_user_traceback(SAMPLE_TRACEBACK)
    assert [f.function for f in frames] == ["main", "do_thing"]
    assert frames[-1].file == "/home/user/project/utils.py"
    assert frames[-1].line == 5


def test_parse_user_traceback_uses_last_block_of_chained_exception():
    frames = parse_user_traceback(CHAINED_TRACEBACK)
    assert len(frames) == 1
    assert frames[0].file == "b.py"
    assert frames[0].function == "second"


def test_parse_user_traceback_no_frames_returns_empty_list():
    assert parse_user_traceback("KeyError: 'missing'") == []


def _copy_fixture_repo(tmp_path: Path) -> Path:
    repo_dir = tmp_path / "repo"
    shutil.copytree(FIXTURES_DIR / "buggy_repo", repo_dir)
    return repo_dir


def test_discover_failing_test_finds_deliberate_bug(tmp_path, test_settings):
    repo_dir = _copy_fixture_repo(tmp_path)
    (repo_dir / "tests" / "test_trigger_bug.py").write_text(
        "from buggy_pkg.calc import sum_first_n\n\n"
        "def test_trigger():\n"
        "    sum_first_n([1, 2, 3], 3)\n"
    )

    discovered = discover_failing_test(repo_dir, None, test_settings)

    assert discovered is not None
    assert "IndexError" in discovered.traceback_text


def test_discover_failing_test_returns_none_when_suite_is_green(tmp_path, test_settings):
    repo_dir = _copy_fixture_repo(tmp_path)

    discovered = discover_failing_test(repo_dir, None, test_settings)

    assert discovered is None


def test_discover_failing_test_respects_test_command(tmp_path, test_settings):
    repo_dir = _copy_fixture_repo(tmp_path)
    (repo_dir / "tests" / "test_trigger_bug.py").write_text(
        "from buggy_pkg.calc import sum_first_n\n\n"
        "def test_trigger():\n"
        "    sum_first_n([1, 2, 3], 3)\n"
    )

    discovered = discover_failing_test(repo_dir, "tests/test_calc.py", test_settings)

    assert discovered is None
