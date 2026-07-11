"""
Tests for variable expansion: $?, $NAME, ${NAME}.
"""

import pytest

from termish import CommandContext, CommandResult, execute
from termish.errors import TerminalError
from termish.fs import MemoryFS


@pytest.fixture
def fs():
    """Create a MemoryFS for testing."""
    return MemoryFS()


class TestExitCode:
    """Tests for $? expansion."""

    def test_exit_code_after_success(self, fs):
        assert execute("true; echo $?", fs) == "0\n"

    def test_exit_code_after_failure(self, fs):
        assert execute("false; echo $?", fs) == "1\n"

    def test_exit_code_command_not_found(self, fs):
        out = execute("nosuchcmd; echo $?", fs)
        assert out == "nosuchcmd: command not found\n127\n"

    def test_exit_code_missing_file(self, fs):
        out = execute("cat /missing; echo exit=$?", fs)
        assert out == "cat: /missing: No such file or directory\nexit=1\n"

    def test_exit_code_in_double_quotes(self, fs):
        assert execute('false; echo "code: $?"', fs) == "code: 1\n"

    def test_exit_code_literal_in_single_quotes(self, fs):
        assert execute("false; echo '$?'", fs) == "$?\n"

    def test_exit_code_starts_at_zero(self, fs):
        assert execute("echo $?", fs) == "0\n"

    def test_exit_code_resets_after_success(self, fs):
        assert execute("false; true; echo $?", fs) == "0\n"

    def test_exit_code_after_or_recovery(self, fs):
        # bash: $? is the exit of the last executed command (the rescue)
        assert execute("false || true; echo $?", fs) == "0\n"

    def test_exit_code_custom_command(self, fs):
        """The retrospective case: a plugin rejects an unknown flag with
        exit 2; the agent checks with echo exit=$?."""

        def curl(ctx: CommandContext) -> CommandResult | None:
            if any(a.startswith("--") for a in ctx.args):
                return CommandResult(exit_code=2, stderr="unrecognized option")
            ctx.stdout.write("ok\n")
            return None

        out = execute("curl --max-time 5 x; echo exit=$?", fs, commands={"curl": curl})
        assert out == "unrecognized option\nexit=2\n"

    def test_exit_code_not_a_glob(self, fs):
        """$? must expand before glob detection ('?' is a glob char)."""
        fs.write("/x", b"")  # a one-char filename that '?' would match
        assert execute("true; echo $?", fs) == "0\n"


class TestVarExpansion:
    """Tests for $NAME / ${NAME} expansion."""

    def test_env_param(self, fs):
        assert execute("echo $NAME", fs, env={"NAME": "alice"}) == "alice\n"

    def test_braced(self, fs):
        assert execute("echo ${NAME}x", fs, env={"NAME": "alice"}) == "alicex\n"

    def test_double_quotes_expand(self, fs):
        out = execute('echo "hi $NAME"', fs, env={"NAME": "alice"})
        assert out == "hi alice\n"

    def test_single_quotes_literal(self, fs):
        assert execute("echo '$NAME'", fs, env={"NAME": "alice"}) == "$NAME\n"

    def test_unset_expands_empty(self, fs):
        assert execute('echo "[$UNSET]"', fs) == "[]\n"

    def test_unset_unquoted_word_removal(self, fs):
        # bash: an unquoted arg that expands to nothing disappears
        assert execute("echo a $UNSET b", fs) == "a b\n"

    def test_escaped_dollar_in_double_quotes(self, fs):
        out = execute('echo "\\$NAME"', fs, env={"NAME": "alice"})
        assert out == "$NAME\n"

    def test_expansion_in_redirect_target(self, fs):
        execute("echo hi > $OUT", fs, env={"OUT": "/o.txt"})
        assert fs.read("/o.txt") == b"hi\n"

    def test_expansion_in_input_redirect(self, fs):
        fs.write("/in.txt", b"data\n")
        assert execute("cat < $IN", fs, env={"IN": "/in.txt"}) == "data\n"

    def test_expansion_in_command_name(self, fs):
        assert execute("$CMD hello", fs, env={"CMD": "echo"}) == "hello\n"

    def test_expanded_value_globs(self, fs):
        fs.write("/a.txt", b"")
        fs.write("/b.txt", b"")
        out = execute("echo $PAT", fs, env={"PAT": "*.txt"})
        assert sorted(out.split()) == ["/a.txt", "/b.txt"]

    def test_env_mutation_visible_to_later_commands(self, fs):
        def setvar(ctx: CommandContext) -> CommandResult | None:
            ctx.env["FOO"] = "bar"
            return None

        out = execute("setvar; echo $FOO", fs, commands={"setvar": setvar})
        assert out == "bar\n"

    def test_env_mutation_visible_to_caller(self, fs):
        def setvar(ctx: CommandContext) -> CommandResult | None:
            ctx.env["FOO"] = "bar"
            return None

        env: dict[str, str] = {}
        execute("setvar", fs, commands={"setvar": setvar}, env=env)
        assert env == {"FOO": "bar"}


class TestNonExpansion:
    """Forms with '$' that must stay literal."""

    def test_trailing_dollar_anchor(self, fs):
        """grep/sed end-of-line anchors survive unquoted."""
        fs.write("/f.txt", b"foo\nfoobar\n")
        assert execute("grep foo$ /f.txt", fs) == "foo\n"

    def test_dollar_digit_literal(self, fs):
        assert execute("echo $1", fs) == "$1\n"

    def test_command_substitution_rejected(self, fs):
        """$(...) is unsupported; fail loudly rather than mangle args."""
        from termish import ParseError

        with pytest.raises(ParseError, match="Command substitution"):
            execute("echo $(pwd)", fs)

    def test_command_substitution_rejected_in_double_quotes(self, fs):
        from termish import ParseError

        with pytest.raises(ParseError, match="Command substitution"):
            execute('echo "now: $(pwd)"', fs)

    def test_command_substitution_literal_in_single_quotes(self, fs):
        """Single quotes protect $(...) as a literal, as in bash."""
        assert execute("echo '$(pwd)'", fs) == "$(pwd)\n"

    def test_heredoc_body_never_expands(self, fs):
        out = execute("cat <<EOF\nhi $NAME\nEOF", fs, env={"NAME": "alice"})
        assert out == "hi $NAME\n"


class TestRetrospective:
    """End-to-end reproduction of the observed agent friction."""

    def test_curl_spiral_line(self, fs):
        def curl(ctx: CommandContext) -> CommandResult | None:
            ctx.stdout.write('{"status":"ok"}\n')
            return None

        fs.makedirs("/app")
        out = execute(
            "cd /app && curl -v 'api/overview' 2>&1 | head -30; "
            'echo "=== EXIT $? ==="',
            fs,
            commands={"curl": curl},
        )
        assert out == '{"status":"ok"}\n=== EXIT 0 ===\n'

    def test_last_pipeline_failure_still_raises(self, fs):
        """Expansion must not change failure propagation."""
        with pytest.raises(TerminalError) as exc_info:
            execute("echo start; cat /missing", fs)
        assert exc_info.value.partial_output == "start\n"
        assert exc_info.value.exit_code == 1
