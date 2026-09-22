from app.config import Settings
from app.context import extract_code_context
from app.models import TracebackFrame


def _settings(**overrides) -> Settings:
    base = dict(
        anthropic_api_key="test-key-not-real",
        max_context_files=5,
        max_context_lines_per_file=10,
        max_context_chars=1000,
    )
    base.update(overrides)
    return Settings(**base)


def test_extract_code_context_direct_relative_match(tmp_path):
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "mod.py").write_text("\n".join(f"line {i}" for i in range(1, 21)))

    frames = [TracebackFrame(file="pkg/mod.py", line=10, function="f")]

    contexts = extract_code_context(tmp_path, frames, _settings())

    assert len(contexts) == 1
    assert contexts[0].file == "pkg/mod.py"
    assert "line 10" in contexts[0].source


def test_extract_code_context_matches_by_suffix_for_absolute_user_path(tmp_path):
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "mod.py").write_text("\n".join(f"line {i}" for i in range(1, 21)))

    frames = [TracebackFrame(file="/home/someone/project/pkg/mod.py", line=5, function="f")]

    contexts = extract_code_context(tmp_path, frames, _settings())

    assert len(contexts) == 1
    assert contexts[0].file == "pkg/mod.py"


def test_extract_code_context_skips_unmatched_frames(tmp_path):
    frames = [TracebackFrame(file="/usr/lib/python3.11/site-packages/foo.py", line=1, function="f")]

    contexts = extract_code_context(tmp_path, frames, _settings())

    assert contexts == []


def test_extract_code_context_respects_max_context_files(tmp_path):
    for i in range(3):
        (tmp_path / f"mod{i}.py").write_text("x = 1\n" * 20)

    frames = [TracebackFrame(file=f"mod{i}.py", line=1, function="f") for i in range(3)]

    contexts = extract_code_context(tmp_path, frames, _settings(max_context_files=2))

    assert len(contexts) == 2


def test_extract_code_context_prioritizes_innermost_frame(tmp_path):
    (tmp_path / "outer.py").write_text("x = 1\n" * 5)
    (tmp_path / "inner.py").write_text("y = 2\n" * 5)

    frames = [
        TracebackFrame(file="outer.py", line=1, function="f"),
        TracebackFrame(file="inner.py", line=1, function="g"),
    ]

    contexts = extract_code_context(tmp_path, frames, _settings(max_context_files=1))

    assert len(contexts) == 1
    assert contexts[0].file == "inner.py"
