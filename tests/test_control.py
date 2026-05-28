"""Tests for control builtins: true, false."""

import pytest

from termish.errors import TerminalError
from termish.fs import MemoryFS
from termish.interpreter import execute_script
from termish.parser import to_script


@pytest.fixture
def fs():
    return MemoryFS()


class TestTrue:
    def test_no_output_and_succeeds(self, fs):
        assert execute_script(to_script("true"), fs) == ""

    def test_chains_with_and(self, fs):
        assert execute_script(to_script("true && echo ok"), fs) == "ok\n"

    def test_short_circuits_or(self, fs):
        # If || ran `echo skipped` it would appear in stdout — it must not.
        assert execute_script(to_script("true || echo skipped"), fs) == ""


class TestFalse:
    def test_fails_with_terminal_error(self, fs):
        with pytest.raises(TerminalError):
            execute_script(to_script("false"), fs)

    def test_or_recovers_from_false(self, fs):
        assert execute_script(to_script("false || echo recovered"), fs) == "recovered\n"

    def test_and_short_circuits_after_false(self, fs):
        # `false && cmd` is still a script-level failure because the last
        # pipeline (false) failed and && skipped the recovery. Use a
        # trailing || to soak the failure back into success.
        assert (
            execute_script(to_script("false && echo skipped || echo afterwards"), fs)
            == "afterwards\n"
        )


class TestOrTrueIdiom:
    def test_rescues_a_failing_command(self, fs):
        # The canonical agent transcript: `<cmd that may fail> || true`.
        # Use a guaranteed-missing path to provoke the failure.
        assert execute_script(to_script("cat /missing || true"), fs) == ""
