"""awk: the cases termish-ts runs too (``cases/awk.json``, the same file in
each repo), each a script, the files it starts with, and what it prints or
the error it fails with."""

import json
from pathlib import Path

import pytest

from termish.errors import TerminalError
from termish.fs import MemoryFS
from termish.interpreter import execute_script
from termish.parser import to_script

CASES = json.loads((Path(__file__).parent / "cases" / "awk.json").read_text("utf-8"))


def _run(case: dict) -> tuple[str, int, str]:
    fs = MemoryFS()
    for path, text in (case.get("files") or {}).items():
        fs.write(path, text.encode("utf-8"))
    if case.get("cwd") is not None:
        fs.makedirs(case["cwd"], exist_ok=True)
        fs.chdir(case["cwd"])
    try:
        out = execute_script(
            to_script(case["script"]), fs, env=dict(case.get("env") or {})
        )
        return out, 0, ""
    except TerminalError as e:
        return e.partial_output, e.exit_code, e.message


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_awk_case(case):
    stdout, exit_code, error = _run(case)
    if "error" in case:
        assert case["error"] in error + stdout
    if "stdout" in case:
        assert stdout == case["stdout"]
    assert exit_code == case.get("exitCode", 0)
