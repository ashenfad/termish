"""The shared cases: scripts and what they give, the same files in
termish-ts (``tests/cases/``), so both terminals behave alike.

A case's ``stdout`` (or ``stdoutRegex``, searched) is what the script
wrote, the partial output when it failed; ``error`` is a substring of
the failure's message and stderr (a parse error's too); ``exitCode``
the failure's code. ``files`` are written first, and the script runs in
``cwd`` with ``env``.
"""

import json
import posixpath
import re
from pathlib import Path

import pytest

from termish.errors import TerminalError
from termish.fs import MemoryFS
from termish.interpreter import execute_script
from termish.parser import ParseError, to_script

CASES_DIR = Path(__file__).parent / "cases"


def _load() -> list[tuple[str, dict]]:
    out = []
    for path in sorted(CASES_DIR.glob("*.json")):
        for case in json.loads(path.read_text(encoding="utf-8")):
            out.append((f"{path.stem}: {case['name']}", case))
    return out


CASES = _load()


def _run(case: dict) -> tuple[str, TerminalError | ParseError | None]:
    fs = MemoryFS()
    for path, content in case.get("files", {}).items():
        fs.makedirs(posixpath.dirname(path) or "/", exist_ok=True)
        fs.write(path, content.encode("utf-8"))
    if "cwd" in case:
        fs.makedirs(case["cwd"], exist_ok=True)
        fs.chdir(case["cwd"])
    try:
        return execute_script(
            to_script(case["script"]), fs, env=dict(case.get("env", {}))
        ), None
    except ParseError as e:
        return "", e
    except TerminalError as e:
        return e.partial_output, e


@pytest.mark.parametrize("case", [c for _, c in CASES], ids=[i for i, _ in CASES])
def test_case(case: dict) -> None:
    stdout, error = _run(case)
    if "stdout" in case:
        assert stdout == case["stdout"]
    if "stdoutRegex" in case:
        assert re.search(case["stdoutRegex"], stdout), stdout
    if "error" not in case:
        assert error is None, error
    else:
        assert error is not None, "the script should fail"
        if isinstance(error, ParseError):
            assert case["error"] in str(error)
        else:
            assert case["error"] in f"{error.message}\n{error.stderr or ''}"
    if "exitCode" in case:
        assert (error.exit_code if error is not None else 0) == case["exitCode"]
