"""Tests for pluggable command injection."""

import pytest

from termish import CommandContext, CommandResult, execute, execute_script
from termish.errors import TerminalError
from termish.fs import MemoryFS
from termish.parser import to_script


@pytest.fixture
def fs():
    return MemoryFS()


# =============================================================================
# Basic injection
# =============================================================================


class TestBasicInjection:
    def test_injected_command_runs(self, fs):
        """A simple injected command that writes to stdout."""

        def hello(ctx: CommandContext) -> CommandResult | None:
            ctx.stdout.write("hello world\n")
            return None

        output = execute("hello", fs, commands={"hello": hello})
        assert output.strip() == "hello world"

    def test_injected_command_receives_args(self, fs):
        """Injected command receives parsed arguments."""

        def echo_args(ctx: CommandContext) -> CommandResult | None:
            ctx.stdout.write(" ".join(ctx.args) + "\n")
            return None

        output = execute("echo_args foo bar baz", fs, commands={"echo_args": echo_args})
        assert output.strip() == "foo bar baz"

    def test_injected_command_reads_stdin(self, fs):
        """Injected command can read piped stdin."""

        def upper(ctx: CommandContext) -> CommandResult | None:
            ctx.stdout.write(ctx.stdin.read().upper())
            return None

        output = execute("echo hello | upper", fs, commands={"upper": upper})
        assert "HELLO" in output

    def test_injected_command_reads_fs(self, fs):
        """Injected command can access the filesystem."""

        def file_exists(ctx: CommandContext) -> CommandResult | None:
            path = ctx.args[0] if ctx.args else ""
            ctx.stdout.write("yes\n" if ctx.fs.exists(path) else "no\n")
            return None

        execute("echo content > test.txt", fs)
        output = execute(
            "file_exists test.txt", fs, commands={"file_exists": file_exists}
        )
        assert output.strip() == "yes"
        output = execute(
            "file_exists missing.txt", fs, commands={"file_exists": file_exists}
        )
        assert output.strip() == "no"

    def test_no_injected_commands_works(self, fs):
        """commands=None (default) works as before."""
        output = execute("echo hello", fs)
        assert output.strip() == "hello"

    def test_empty_commands_dict_works(self, fs):
        """commands={} works as before — only builtins available."""
        output = execute("echo hello", fs, commands={})
        assert output.strip() == "hello"


# =============================================================================
# Pipeline composition
# =============================================================================


class TestPipelineComposition:
    def test_injected_piped_to_builtin(self, fs):
        """Injected command output piped to a built-in."""

        def gen(ctx: CommandContext) -> CommandResult | None:
            ctx.stdout.write("alpha\nbeta\ngamma\n")
            return None

        output = execute("gen | grep beta", fs, commands={"gen": gen})
        assert output.strip() == "beta"

    def test_builtin_piped_to_injected(self, fs):
        """Built-in output piped to an injected command."""

        def count_chars(ctx: CommandContext) -> CommandResult | None:
            text = ctx.stdin.read()
            ctx.stdout.write(f"{len(text)}\n")
            return None

        output = execute(
            "echo hello | count_chars", fs, commands={"count_chars": count_chars}
        )
        # "hello\n" = 6 chars
        assert output.strip() == "6"

    def test_multi_stage_mixed_pipeline(self, fs):
        """Multi-stage pipeline mixing injected and built-in commands."""

        def gen_numbers(ctx: CommandContext) -> CommandResult | None:
            for i in range(1, 6):
                ctx.stdout.write(f"{i}\n")
            return None

        # gen_numbers | grep -v 3 | wc -l → 4 lines (1,2,4,5)
        output = execute(
            "gen_numbers | grep -v 3 | wc -l",
            fs,
            commands={"gen_numbers": gen_numbers},
        )
        assert output.strip() == "4"


# =============================================================================
# Override
# =============================================================================


class TestOverride:
    def test_injected_overrides_builtin(self, fs):
        """An injected command with the same name as a built-in wins."""

        def custom_echo(ctx: CommandContext) -> CommandResult | None:
            ctx.stdout.write("CUSTOM: " + " ".join(ctx.args) + "\n")
            return None

        output = execute("echo hello", fs, commands={"echo": custom_echo})
        assert output.strip() == "CUSTOM: hello"

    def test_builtin_still_works_for_non_overridden(self, fs):
        """Non-overridden built-ins work alongside injected commands."""

        def custom(ctx: CommandContext) -> CommandResult | None:
            ctx.stdout.write("custom\n")
            return None

        # 'ls' is not overridden, should still work
        output = execute("echo test > file.txt && ls", fs, commands={"custom": custom})
        assert "file.txt" in output


# =============================================================================
# Error propagation
# =============================================================================


class TestErrorPropagation:
    def test_terminal_error_from_injected_command(self, fs):
        """TerminalError raised by injected command surfaces normally."""

        def fail_cmd(ctx: CommandContext) -> CommandResult | None:
            raise TerminalError("something broke")

        with pytest.raises(TerminalError, match="something broke"):
            execute("fail_cmd", fs, commands={"fail_cmd": fail_cmd})

    def test_generic_exception_wrapped_as_terminal_error(self, fs):
        """A non-TerminalError exception is wrapped with context."""

        def bad_cmd(ctx: CommandContext) -> CommandResult | None:
            raise ValueError("oops")

        with pytest.raises(TerminalError, match="bad_cmd: execution error"):
            execute("bad_cmd", fs, commands={"bad_cmd": bad_cmd})

    def test_command_result_nonzero_exit_code(self, fs):
        """CommandResult with non-zero exit_code raises TerminalError."""

        def failing(ctx: CommandContext) -> CommandResult | None:
            ctx.stdout.write("partial output\n")
            return CommandResult(exit_code=1, stderr="disk full")

        with pytest.raises(TerminalError, match="disk full"):
            execute("failing", fs, commands={"failing": failing})

    def test_command_result_zero_exit_code_succeeds(self, fs):
        """CommandResult with exit_code=0 is treated as success."""

        def ok_cmd(ctx: CommandContext) -> CommandResult | None:
            ctx.stdout.write("all good\n")
            return CommandResult(exit_code=0)

        output = execute("ok_cmd", fs, commands={"ok_cmd": ok_cmd})
        assert output.strip() == "all good"

    def test_command_not_found(self, fs):
        """Unknown command still raises command-not-found error."""
        with pytest.raises(TerminalError, match="nosuchcmd: command not found"):
            execute("nosuchcmd", fs, commands={})


# =============================================================================
# xargs with injected commands
# =============================================================================


class TestXargsWithInjection:
    def test_xargs_dispatches_to_injected_command(self, fs):
        """xargs can dispatch to an injected command."""

        def shout(ctx: CommandContext) -> CommandResult | None:
            ctx.stdout.write(" ".join(ctx.args).upper() + "\n")
            return None

        output = execute(
            "echo 'hello world' | xargs shout",
            fs,
            commands={"shout": shout},
        )
        assert "HELLO WORLD" in output

    def test_xargs_with_injected_and_n_flag(self, fs):
        """xargs -n with an injected command processes items in batches."""

        def prefix(ctx: CommandContext) -> CommandResult | None:
            ctx.stdout.write(">> " + " ".join(ctx.args) + "\n")
            return None

        output = execute(
            "echo 'a b c' | xargs -n 1 prefix",
            fs,
            commands={"prefix": prefix},
        )
        assert ">> a" in output
        assert ">> b" in output
        assert ">> c" in output


# =============================================================================
# execute_script with commands parameter
# =============================================================================


class TestExecuteScript:
    def test_execute_script_accepts_commands(self, fs):
        """execute_script() threads commands through correctly."""

        def greet(ctx: CommandContext) -> CommandResult | None:
            ctx.stdout.write(f"hi {ctx.args[0]}\n")
            return None

        script = to_script("greet world")
        output = execute_script(script, fs, commands={"greet": greet})
        assert output.strip() == "hi world"

    def test_chained_pipelines_with_injection(self, fs):
        """Multiple pipelines chained with && use injected commands."""

        call_count = 0

        def counter(ctx: CommandContext) -> CommandResult | None:
            nonlocal call_count
            call_count += 1
            ctx.stdout.write(f"call {call_count}\n")
            return None

        output = execute(
            "counter && counter && counter", fs, commands={"counter": counter}
        )
        assert call_count == 3
        assert "call 3" in output
