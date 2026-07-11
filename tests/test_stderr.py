"""
Tests for stderr visibility: failure diagnostics surface in the
transcript when execution continues past them, exactly as a terminal
would show stderr on screen.
"""

import pytest

from termish import CommandContext, CommandResult, execute
from termish.errors import TerminalError
from termish.fs import MemoryFS


@pytest.fixture
def fs():
    """Create a MemoryFS for testing."""
    return MemoryFS()


def _curl(ctx: CommandContext) -> CommandResult | None:
    """Plugin modeled on the retrospective: rejects unknown flags."""
    for a in ctx.args:
        if a.startswith("--"):
            return CommandResult(exit_code=2, stderr=f"curl: option {a}: is unknown")
    ctx.stdout.write('{"status":"ok"}\n')
    return None


class TestIntermediateFailures:
    """Failures followed by more execution show their diagnostic."""

    def test_semicolon_shows_diagnostic(self, fs):
        out = execute("cat /missing; echo ok", fs)
        assert out == "cat: /missing: No such file or directory\nok\n"

    def test_or_rescue_shows_diagnostic_before_fallback(self, fs):
        out = execute("cat /missing || echo fallback", fs)
        assert out == "cat: /missing: No such file or directory\nfallback\n"

    def test_or_true_swallows_failure_but_shows_stderr(self, fs):
        # bash: `cmd || true` swallows the exit code, not the stderr
        out = execute("cat /missing || true", fs)
        assert out == "cat: /missing: No such file or directory\n"

    def test_command_not_found_shows_diagnostic(self, fs):
        out = execute("nosuchcmd; echo ok", fs)
        assert out == "nosuchcmd: command not found\nok\n"

    def test_false_stays_silent(self, fs):
        # bash: `false` prints nothing
        assert execute("false; echo ok", fs) == "ok\n"
        assert execute("false || echo rescued", fs) == "rescued\n"

    def test_custom_command_stderr_shown(self, fs):
        out = execute("curl --max-time 5 x; echo exit=$?", fs, commands={"curl": _curl})
        assert out == "curl: option --max-time: is unknown\nexit=2\n"

    def test_multiple_failures_all_shown(self, fs):
        out = execute("cat /a; cat /b; echo done", fs)
        assert out == (
            "cat: /a: No such file or directory\n"
            "cat: /b: No such file or directory\n"
            "done\n"
        )


class TestFinalFailures:
    """A failure with nothing after it still raises — no duplication."""

    def test_last_failure_raises_without_transcript_diag(self, fs):
        with pytest.raises(TerminalError) as exc_info:
            execute("echo start; cat /missing", fs)
        assert exc_info.value.partial_output == "start\n"
        assert exc_info.value.message == "cat: /missing: No such file or directory"
        assert exc_info.value.exit_code == 1

    def test_and_skip_then_end_raises(self, fs):
        # && skips the rest; the failure is the outcome, so it raises
        with pytest.raises(TerminalError):
            execute("cat /missing && echo yes", fs)

    def test_earlier_diagnostic_survives_in_partial_output(self, fs):
        with pytest.raises(TerminalError) as exc_info:
            execute("cat /a; echo mid; cat /b", fs)
        assert exc_info.value.partial_output == (
            "cat: /a: No such file or directory\nmid\n"
        )
        assert exc_info.value.message == "cat: /b: No such file or directory"

    def test_stderr_attr_carries_handler_stderr(self, fs):
        with pytest.raises(TerminalError) as exc_info:
            execute("curl --fail x", fs, commands={"curl": _curl})
        assert exc_info.value.stderr == "curl: option --fail: is unknown"
        assert exc_info.value.exit_code == 2


class TestSuccessWithStderr:
    """exit 0 + stderr (warnings) surfaces in the transcript."""

    def test_warning_shown_with_stdout(self, fs):
        def warn(ctx: CommandContext) -> CommandResult | None:
            ctx.stdout.write("result\n")
            return CommandResult(exit_code=0, stderr="warn: deprecated flag\n")

        out = execute("warn", fs, commands={"warn": warn})
        assert out == "warn: deprecated flag\nresult\n"

    def test_warning_not_fed_to_pipe(self, fs):
        def warn(ctx: CommandContext) -> CommandResult | None:
            ctx.stdout.write("a\nb\n")
            return CommandResult(exit_code=0, stderr="warn: something\n")

        # wc counts only stdout lines; the warning goes to the transcript
        out = execute("warn | wc -l", fs, commands={"warn": warn})
        assert out == "warn: something\n2\n"


class TestRetrospectiveSpiral:
    """The full observed line, now with visible errors AND exit code."""

    def test_rejected_flag_is_fully_visible(self, fs):
        fs.makedirs("/app")
        out = execute(
            "cd /app && curl --max-time 5 'api/overview' 2>&1 | head -30; "
            'echo "=== EXIT $? ==="',
            fs,
            commands={"curl": _curl},
        )
        assert out == "curl: option --max-time: is unknown\n=== EXIT 2 ===\n"

    def test_happy_path_unchanged(self, fs):
        fs.makedirs("/app")
        out = execute(
            "cd /app && curl 'api/overview' 2>&1 | head -30; echo \"=== EXIT $? ===\"",
            fs,
            commands={"curl": _curl},
        )
        assert out == '{"status":"ok"}\n=== EXIT 0 ===\n'
