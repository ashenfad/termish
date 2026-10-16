"""Redirect targets under the silent-failure rules. The shared cases in
``cases/silent-failures.json`` run with the others in ``test_cases.py``."""

import pytest

from termish import MemoryFS, TerminalError, execute


@pytest.mark.parametrize("op", [">", ">>", "2>", "2>>"])
def test_unset_redirect_target_leaves_nothing_done(op):
    """A redirect target expands before its command runs: an unset
    variable in one stops the command with nothing done."""
    fs = MemoryFS()
    fs.write("/victim", b"x")
    for script in (f"rm /victim {op} $NOPE", f"touch /made {op} $NOPE"):
        with pytest.raises(TerminalError, match="NOPE: unbound variable"):
            execute(script, fs)
    assert fs.exists("/victim")
    assert not fs.exists("/made")


def test_redirect_target_resolves_where_the_command_starts():
    fs = MemoryFS()
    fs.makedirs("/sub")
    execute("cd sub > log", fs)
    assert fs.exists("/log")
    assert not fs.exists("/sub/log")
