from __future__ import annotations

import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from app.config import Settings
from app.models import DiscoveredFailure, TracebackFrame
from app.subprocess_utils import run_subprocess

_FRAME_RE = re.compile(r'File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>\S+)')
_CHAIN_SPLIT_RE = re.compile(
    r"\n(?:During handling of the above exception, another exception occurred:"
    r"|The above exception was the direct cause of the following exception:)\n"
)


def parse_user_traceback(text: str) -> list[TracebackFrame]:
    """Extracts frames from the last exception block of a (possibly chained) traceback."""
    blocks = _CHAIN_SPLIT_RE.split(text)
    last_block = blocks[-1] if blocks else text
    return [
        TracebackFrame(file=m.group("file"), line=int(m.group("line")), function=m.group("func"))
        for m in _FRAME_RE.finditer(last_block)
    ]


def _best_effort_install(repo_dir: Path, settings: Settings) -> None:
    """Attempts to install the target repo's dependencies so its test suite can run.

    Best-effort only: failures are swallowed and pytest is attempted regardless,
    since many small repos need nothing beyond what's already on PATH.
    """
    if (repo_dir / "requirements.txt").exists():
        args = ["pip", "install", "-q", "-r", "requirements.txt"]
    elif (repo_dir / "pyproject.toml").exists() or (repo_dir / "setup.py").exists():
        args = ["pip", "install", "-q", "-e", "."]
    else:
        return
    run_subprocess(args, cwd=repo_dir, timeout=settings.pip_install_timeout_seconds)


def discover_failing_test(
    repo_dir: Path, test_command: str | None, settings: Settings
) -> DiscoveredFailure | None:
    """Runs the repo's own test suite to find a failing test, returning None if none found."""
    _best_effort_install(repo_dir, settings)

    with tempfile.TemporaryDirectory() as tmp:
        junit_path = Path(tmp) / "junit.xml"
        args = ["pytest", "--tb=native", "-q", f"--junit-xml={junit_path}"]
        if test_command:
            args.append(test_command)
        run_subprocess(args, cwd=repo_dir, timeout=settings.pytest_discovery_timeout_seconds)

        if not junit_path.exists():
            return None

        tree = ET.parse(junit_path)
        for testcase in tree.getroot().iter("testcase"):
            node = testcase.find("failure")
            if node is None:
                node = testcase.find("error")
            if node is None:
                continue

            traceback_text = node.text or node.get("message", "") or ""
            file_attr = testcase.get("file")
            name_attr = testcase.get("name", "")
            nodeid = f"{file_attr}::{name_attr}" if file_attr else name_attr

            return DiscoveredFailure(
                test_nodeid=nodeid,
                traceback_text=traceback_text,
                frames=parse_user_traceback(traceback_text),
            )

    return None
