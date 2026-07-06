"""Here-documents and exit-code fidelity."""

import pytest

from termish import MemoryFS, ParseError, TerminalError, execute
from termish.parser import to_script

# -- parsing -----------------------------------------------------------------


def test_parse_basic_heredoc():
    script = to_script("cat <<EOF\nhello\nworld\nEOF")
    (pipeline,) = script.pipelines
    (cmd,) = pipeline.commands
    (redirect,) = cmd.redirects
    assert redirect.type == "<<"
    assert redirect.target == "EOF"
    assert redirect.content == "hello\nworld\n"


def test_parse_quoted_delimiters():
    for form in ("<<'EOF'", '<<"EOF"', "<< 'EOF'"):
        script = to_script(f"cat {form}\nbody\nEOF")
        assert script.pipelines[0].commands[0].redirects[0].content == "body\n"


def test_empty_body():
    script = to_script("cat <<EOF\nEOF")
    assert script.pipelines[0].commands[0].redirects[0].content == ""


def test_body_is_raw():
    """Quotes, operators, and redirects in the body are inert."""
    body = "a | b > c ; 'quoted' \"double\" && <<nested\n"
    script = to_script(f"cat <<END\n{body}END")
    assert script.pipelines[0].commands[0].redirects[0].content == body


def test_indented_delimiter_accepted():
    script = to_script("cat <<EOF\nbody\n    EOF")
    assert script.pipelines[0].commands[0].redirects[0].content == "body\n"


def test_unterminated_heredoc():
    with pytest.raises(ParseError, match="Unterminated heredoc"):
        to_script("cat <<EOF\nno end in sight")


def test_missing_delimiter():
    with pytest.raises(ParseError, match="Expected delimiter"):
        to_script("cat <<\nbody\nEOF")


def test_heredoc_op_inside_quotes_ignored():
    script = to_script("echo '<<EOF not a heredoc'")
    (cmd,) = script.pipelines[0].commands
    assert not cmd.redirects


# -- execution ----------------------------------------------------------------


def test_cat_heredoc():
    fs = MemoryFS()
    out = execute("cat <<EOF\nline one\nline two\nEOF", fs)
    assert out == "line one\nline two\n"


def test_heredoc_into_pipeline():
    fs = MemoryFS()
    out = execute("cat <<EOF | sort\nbanana\napple\nEOF", fs)
    assert out.splitlines() == ["apple", "banana"]


def test_heredoc_to_file_via_tee():
    """The write-a-script idiom heredocs exist for."""
    fs = MemoryFS()
    execute("tee script.py <<'PY'\nprint('hi')\nprint(1 + 1)\nPY", fs)
    assert fs.read("script.py").decode() == "print('hi')\nprint(1 + 1)\n"


def test_heredoc_with_commands_after():
    fs = MemoryFS()
    out = execute("cat <<EOF && echo done\npayload\nEOF", fs)
    assert out == "payload\ndone\n"


def test_two_heredocs_sequential_pipelines():
    fs = MemoryFS()
    out = execute("cat <<A; cat <<B\nfirst\nA\nsecond\nB", fs)
    assert out == "first\nsecond\n"


def test_heredoc_stdin_reaches_injected_command():
    def shout(ctx):
        ctx.stdout.write(ctx.stdin.read().upper())
        return None

    fs = MemoryFS()
    out = execute("shout <<EOF\nquiet words\nEOF", fs, commands={"shout": shout})
    assert out == "QUIET WORDS\n"


# -- exit codes ----------------------------------------------------------------


def test_command_not_found_is_127():
    with pytest.raises(TerminalError) as e:
        execute("definitely_not_a_command", MemoryFS())
    assert e.value.exit_code == 127


def test_command_result_code_preserved():
    from termish import CommandResult

    def flaky(ctx):
        return CommandResult(exit_code=22, stderr="HTTP 404")

    with pytest.raises(TerminalError) as e:
        execute("flaky", MemoryFS(), commands={"flaky": flaky})
    assert e.value.exit_code == 22
    assert "HTTP 404" in e.value.message


def test_false_builtin_is_1():
    with pytest.raises(TerminalError) as e:
        execute("false", MemoryFS())
    assert e.value.exit_code == 1


def test_exit_code_survives_multi_pipeline_script():
    from termish import CommandResult

    def failing(ctx):
        return CommandResult(exit_code=42, stderr="boom")

    with pytest.raises(TerminalError) as e:
        execute("echo ok; failing", MemoryFS(), commands={"failing": failing})
    assert e.value.exit_code == 42
    assert "ok" in e.value.partial_output


def test_default_exit_code_is_1():
    assert TerminalError("msg").exit_code == 1  # backward compatible


# -- review fixes: continuation and comments (PR #14) -------------------------


def test_continuation_before_heredoc_joins_command_line():
    fs = MemoryFS()
    out = execute("cat <<EOF \\\n | tr a-z A-Z\nshout this\nEOF", fs)
    assert out == "SHOUT THIS\n"


def test_backslash_in_body_stays_raw():
    """The inverse hazard: continuation joining must NOT touch bodies."""
    fs = MemoryFS()
    out = execute("cat <<EOF\nC:\\path\\\nnext line\nEOF", fs)
    assert out == "C:\\path\\\nnext line\n"


def test_heredoc_op_in_comment_ignored():
    fs = MemoryFS()
    out = execute("echo hi # <<EOF not a heredoc", fs)
    assert out.strip() == "hi"


def test_hash_inside_word_not_a_comment():
    script = to_script("cat <<E#F\nbody\nE#F")
    assert script.pipelines[0].commands[0].redirects[0].content == "body\n"


def test_hash_inside_quotes_not_a_comment():
    fs = MemoryFS()
    out = execute("echo '#<<EOF literal'", fs)
    assert out.strip() == "#<<EOF literal"
