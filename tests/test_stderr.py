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


class TestStderrToFile:
    """`2>file` / `2>>file` / `2>/dev/null` route stderr instead of no-op."""

    def test_capture_to_file(self, fs):
        out = execute("cat /missing 2>/err.txt; echo ok", fs)
        assert out == "ok\n"  # diagnostic consumed by the redirect
        assert fs.read("/err.txt") == b"cat: /missing: No such file or directory\n"

    def test_dev_null_suppresses(self, fs):
        out = execute("cat /missing 2>/dev/null; echo ok", fs)
        assert out == "ok\n"
        assert not fs.exists("/dev/null")

    def test_dev_null_preserves_exit_code(self, fs):
        out = execute("cat /missing 2>/dev/null; echo exit=$?", fs)
        assert out == "exit=1\n"

    def test_truncates_even_without_stderr(self, fs):
        # bash creates/truncates the target regardless of stderr output
        fs.write("/err.txt", b"old content")
        out = execute("echo hi 2>/err.txt", fs)
        assert out == "hi\n"
        assert fs.read("/err.txt") == b""

    def test_append_form(self, fs):
        execute("cat /a 2>>/err.txt; cat /b 2>>/err.txt; true", fs)
        assert fs.read("/err.txt") == (
            b"cat: /a: No such file or directory\ncat: /b: No such file or directory\n"
        )

    def test_redirected_failure_still_aborts_and_raises_silently(self, fs):
        with pytest.raises(TerminalError) as exc_info:
            execute("cat /missing 2>/dev/null", fs)
        assert exc_info.value.exit_code == 1
        assert exc_info.value.stderr == ""  # diagnostic was consumed

    def test_command_not_found_suppressible(self, fs):
        out = execute("nosuchcmd 2>/dev/null; echo ok", fs)
        assert out == "ok\n"

    def test_custom_command_stderr_to_file(self, fs):
        out = execute(
            "curl --fail x 2>/err.txt; echo exit=$?", fs, commands={"curl": _curl}
        )
        assert out == "exit=2\n"
        assert fs.read("/err.txt") == b"curl: option --fail: is unknown\n"

    def test_variable_in_target(self, fs):
        execute("cat /missing 2>$LOG; true", fs, env={"LOG": "/my.log"})
        assert fs.read("/my.log").startswith(b"cat: /missing: ")


class TestStderrMerge:
    """`2>&1` merges stderr into the stdout pipe."""

    def test_error_flows_through_pipe(self, fs):
        # THE agent idiom: capture error text through the pipe
        out = execute("cat /missing 2>&1 | head -1", fs)
        assert out == "cat: /missing: No such file or directory\n"

    def test_merged_midpipe_failure_does_not_abort(self, fs):
        # bash without pipefail: pipeline exit comes from the last stage
        out = execute("cat /missing 2>&1 | wc -l; echo exit=$?", fs)
        assert out == " 1\nexit=0\n"

    def test_final_stage_merge_failure_raises_with_exit_code(self, fs):
        with pytest.raises(TerminalError) as exc_info:
            execute("cat /missing 2>&1", fs)
        assert exc_info.value.exit_code == 1
        # diagnostic already reached the transcript via the merge
        assert exc_info.value.partial_output == (
            "cat: /missing: No such file or directory\n"
        )
        assert exc_info.value.stderr == ""

    def test_merge_to_output_redirect(self, fs):
        # cmd > f 2>&1 — merged content lands in the file
        execute("cat /missing 2>&1 > /all.txt; true", fs)
        assert fs.read("/all.txt") == b"cat: /missing: No such file or directory\n"

    def test_success_warning_joins_pipe(self, fs):
        def warn(ctx: CommandContext) -> CommandResult | None:
            ctx.stdout.write("a\nb\n")
            return CommandResult(exit_code=0, stderr="warn: something\n")

        # with 2>&1 the warning is IN the pipe (3 lines), not the transcript
        out = execute("warn 2>&1 | wc -l", fs, commands={"warn": warn})
        assert out == " 3\n"

    def test_stdout_unaffected_on_success(self, fs):
        assert execute("echo hi 2>&1", fs) == "hi\n"
        assert execute("echo hi 2>&1 | wc -l", fs) == "1\n"


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
        # bash parity: with 2>&1 the error text flows through the pipe and
        # $? is head's status (0, no pipefail) — the failure is VISIBLE.
        assert out == "curl: option --max-time: is unknown\n=== EXIT 0 ===\n"

    def test_rejected_flag_without_merge_keeps_exit_code(self, fs):
        # without 2>&1 the pipeline aborts: diagnostic + exit 2
        out = execute(
            "curl --max-time 5 'api/overview' | head -30; echo \"=== EXIT $? ===\"",
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
