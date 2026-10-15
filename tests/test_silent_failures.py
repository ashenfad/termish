"""The cases in ``cases/silent-failures.json``: braces, ``$PWD`` and
``$HOME``, unset variables, and the parser's diagnostics. @agex-ts/termish
runs the same file, so the two stay alike."""

import json
from pathlib import Path

import pytest

from termish import MemoryFS, ParseError, TerminalError, execute

CASES = json.loads(
    (Path(__file__).parent / "cases" / "silent-failures.json").read_text()
)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_case(case):
    """Each case: ``script``, run with ``files`` written first, in
    ``cwd``, with ``env``. ``stdout`` is the transcript (what's returned,
    or a failure's partial output); ``error`` a substring of the error
    the script fails with; ``exitCode`` a terminal error's exit code."""
    fs = MemoryFS()
    for path, content in case.get("files", {}).items():
        fs.makedirs(path.rsplit("/", 1)[0] or "/", exist_ok=True)
        fs.write(path, content.encode())
    if "cwd" in case:
        fs.makedirs(case["cwd"], exist_ok=True)
        fs.chdir(case["cwd"])
    env = dict(case.get("env", {}))
    if "error" not in case:
        assert execute(case["script"], fs, env=env) == case["stdout"]
        return
    with pytest.raises((ParseError, TerminalError)) as info:
        execute(case["script"], fs, env=env)
    assert case["error"] in str(info.value)
    if "exitCode" in case:
        assert info.value.exit_code == case["exitCode"]
    if "stdout" in case:
        assert info.value.partial_output == case["stdout"]
