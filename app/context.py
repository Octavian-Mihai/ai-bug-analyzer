from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.models import CodeContext, TracebackFrame


def _find_matching_file(repo_dir: Path, frame_file: str) -> Path | None:
    """Locates the frame's source file inside repo_dir.

    Auto-discovered frames are typically already relative to repo_dir. User-supplied
    tracebacks carry absolute paths from wherever the user originally ran the code,
    which won't exist in our clone, so we fall back to matching by filename plus
    shrinking path suffixes and requiring the match to stay inside the repo.
    """
    frame_path = Path(frame_file)

    direct = repo_dir / frame_path
    if direct.is_file():
        try:
            if direct.resolve().is_relative_to(repo_dir.resolve()):
                return direct
        except OSError:
            pass

    parts = frame_path.parts
    for suffix_len in range(min(len(parts), 4), 0, -1):
        suffix = Path(*parts[-suffix_len:])
        candidates = [
            p for p in repo_dir.rglob(suffix.name)
            if p.is_file() and str(p.resolve()).endswith(str(suffix))
        ]
        if len(candidates) == 1:
            return candidates[0]

    return None


def extract_code_context(
    repo_dir: Path, frames: list[TracebackFrame], settings: Settings
) -> list[CodeContext]:
    """Builds a bounded source-code context bundle from traceback frames.

    Prioritizes the innermost frame (last one printed) and stops once
    max_context_files or max_context_chars is reached, so the LLM prompt stays
    scoped instead of dumping the whole repo.
    """
    repo_dir = repo_dir.resolve()
    contexts: list[CodeContext] = []
    seen_files: set[Path] = set()
    total_chars = 0
    half_window = max(1, settings.max_context_lines_per_file // 2)

    for frame in reversed(frames):
        if len(contexts) >= settings.max_context_files or total_chars >= settings.max_context_chars:
            break

        matched = _find_matching_file(repo_dir, frame.file)
        if matched is None or matched in seen_files:
            continue
        seen_files.add(matched)

        try:
            text = matched.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        lines = text.splitlines()
        start = max(1, frame.line - half_window)
        end = min(len(lines), frame.line + half_window)
        snippet = "\n".join(lines[start - 1 : end])

        remaining_budget = settings.max_context_chars - total_chars
        if remaining_budget <= 0:
            break
        snippet = snippet[:remaining_budget]
        total_chars += len(snippet)

        contexts.append(
            CodeContext(
                file=str(matched.relative_to(repo_dir)),
                start_line=start,
                end_line=end,
                source=snippet,
            )
        )

    return contexts
