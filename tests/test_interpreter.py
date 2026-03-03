import pytest

from termish.errors import TerminalError
from termish.fs import MemoryFS
from termish.interpreter import execute_script
from termish.parser import to_script


@pytest.fixture
def fs():
    return MemoryFS()


def test_simple_echo(fs):
    script = to_script("echo hello world")
    output = execute_script(script, fs)
    assert output == "hello world\n"


def test_file_operations(fs):
    # 1. mkdir
    execute_script(to_script("mkdir -p data"), fs)
    assert fs.isdir("/data")

    # 2. cd and pwd
    execute_script(to_script("cd data"), fs)
    assert fs.getcwd() == "/data"

    output = execute_script(to_script("pwd"), fs)
    assert output.strip() == "/data"

    # 3. echo redirect
    execute_script(to_script("echo 'foo bar' > test.txt"), fs)
    assert fs.read("test.txt") == b"foo bar\n"

    # 4. cat
    output = execute_script(to_script("cat test.txt"), fs)
    assert output == "foo bar\n"


def test_pipeline(fs):
    # Setup
    execute_script(to_script("echo 'line 1\nline 2\nline 3' > lines.txt"), fs)

    # Pipe: cat | head
    output = execute_script(to_script("cat lines.txt | head -n 2"), fs)
    assert output == "line 1\nline 2\n"


def test_find_glob(fs):
    # Setup
    execute_script(to_script("touch a.py b.py c.txt"), fs)

    # Glob ls
    output = execute_script(to_script("ls *.py"), fs)
    assert "a.py" in output
    assert "b.py" in output
    assert "c.txt" not in output


def test_quoted_wildcard(fs):
    # Verify masking works (no glob expansion)

    # Method 1: Pipe
    # echo '*' -> prints *
    # grep -F '*' -> searches for literal *
    output = execute_script(to_script("echo '*' | grep -F '*'"), fs)
    assert "*" in output

    # Method 2: File
    execute_script(to_script("echo '*' > star.txt"), fs)
    output = execute_script(to_script("grep -F '*' star.txt"), fs)
    assert "*" in output


def test_semicolon_continues_on_error(fs):
    """Semicolons (and newlines) always continue, matching bash behavior."""
    script_text = """
    cd /nonexistent
    echo 'Should still run'
    """

    # With bash-style ;, the second command runs despite the first failing.
    # The last pipeline succeeded, so no error is raised.
    output = execute_script(to_script(script_text), fs)
    assert "Should still run" in output


def test_semicolon_raises_if_last_fails(fs):
    """If the last pipeline fails, the error is still raised."""
    with pytest.raises(TerminalError) as excinfo:
        execute_script(to_script("echo ok ; cd /nonexistent"), fs)
    assert "cd: no such file" in str(excinfo.value)
    assert "ok" in excinfo.value.partial_output


def test_ls_unknown_option(fs):
    """Test that ls errors on unknown options."""
    with pytest.raises(TerminalError) as excinfo:
        execute_script(to_script("ls --unknown"), fs)
    assert "unknown option" in str(excinfo.value)


def test_cat_unknown_option(fs):
    """Test that cat errors on unknown options."""
    execute_script(to_script("echo 'test' > test.txt"), fs)
    with pytest.raises(TerminalError) as excinfo:
        execute_script(to_script("cat --unknown test.txt"), fs)
    assert "unknown option" in str(excinfo.value)


def test_diff_unknown_option(fs):
    """Test that diff errors on unknown options."""
    execute_script(to_script("echo 'a' > a.txt"), fs)
    execute_script(to_script("echo 'b' > b.txt"), fs)
    with pytest.raises(TerminalError) as excinfo:
        execute_script(to_script("diff --unknown a.txt b.txt"), fs)
    assert "unknown option" in str(excinfo.value)


# ---------------------------------------------------------------------------
# echo -n / -e
# ---------------------------------------------------------------------------


class TestEchoFlags:
    def test_echo_n(self, fs):
        out = execute_script(to_script("echo -n hello"), fs)
        assert out == "hello"

    def test_echo_e_newline(self, fs):
        out = execute_script(to_script("echo -e 'hello\\nworld'"), fs)
        assert out == "hello\nworld\n"

    def test_echo_e_tab(self, fs):
        out = execute_script(to_script("echo -e 'a\\tb'"), fs)
        assert out == "a\tb\n"

    def test_echo_ne_combined(self, fs):
        out = execute_script(to_script("echo -ne 'hello\\n'"), fs)
        assert out == "hello\n"

    def test_echo_unknown_flag_is_literal(self, fs):
        out = execute_script(to_script("echo -z test"), fs)
        assert out == "-z test\n"


# ---------------------------------------------------------------------------
# head -N / tail -N / tail +N
# ---------------------------------------------------------------------------


class TestHeadTailShorthand:
    def test_head_shorthand(self, fs):
        fs.write("/f.txt", b"a\nb\nc\nd\ne\n")
        out = execute_script(to_script("head -3 f.txt"), fs)
        assert out == "a\nb\nc\n"

    def test_tail_shorthand(self, fs):
        fs.write("/f.txt", b"a\nb\nc\nd\ne\n")
        out = execute_script(to_script("tail -2 f.txt"), fs)
        assert out == "d\ne\n"

    def test_tail_from_line(self, fs):
        fs.write("/f.txt", b"a\nb\nc\nd\ne\n")
        out = execute_script(to_script("tail -n +3 f.txt"), fs)
        assert out == "c\nd\ne\n"


# ---------------------------------------------------------------------------
# ls -h / -t
# ---------------------------------------------------------------------------


class TestLsFlags:
    def test_ls_human_readable(self, fs):
        fs.write("/big.txt", b"x" * 2048)
        out = execute_script(to_script("ls -lh /big.txt"), fs)
        assert "2.0K" in out

    def test_ls_sort_by_time(self, fs):
        fs.write("/a.txt", b"a")
        fs.write("/b.txt", b"b")
        # b.txt written second, should appear first with -t
        out = execute_script(to_script("ls -lt /"), fs)
        lines = [line for line in out.strip().split("\n") if line]
        # b.txt should come before a.txt
        b_idx = next(i for i, line in enumerate(lines) if "b.txt" in line)
        a_idx = next(i for i, line in enumerate(lines) if "a.txt" in line)
        assert b_idx < a_idx


# ---------------------------------------------------------------------------
# diff -i
# ---------------------------------------------------------------------------


class TestDiffIgnoreCase:
    def test_diff_ignore_case(self, fs):
        fs.write("/a.txt", b"Hello World\n")
        fs.write("/b.txt", b"hello world\n")
        # Without -i, should show differences
        out_diff = execute_script(to_script("diff a.txt b.txt"), fs)
        assert len(out_diff) > 0
        # With -i, should show no differences
        out_same = execute_script(to_script("diff -i a.txt b.txt"), fs)
        assert out_same == ""

    def test_diff_ignore_case_shows_original_lines(self, fs):
        fs.write("/a.txt", b"Hello\nSAME\n")
        fs.write("/b.txt", b"World\nSAME\n")
        out = execute_script(to_script("diff -i a.txt b.txt"), fs)
        # Output should contain original-cased lines, not lowercased
        assert "Hello" in out
        assert "World" in out
        assert "hello" not in out
        assert "world" not in out

    def test_diff_ignore_case_duplicate_preprocessed_lines(self, fs):
        """Lines that differ in case but match after -i should map back correctly."""
        fs.write("/a.txt", b"Alpha\nalpha\nBeta\n")
        fs.write("/b.txt", b"Alpha\nalpha\nGamma\n")
        out = execute_script(to_script("diff -i a.txt b.txt"), fs)
        # Should show original-cased lines, not lowercased
        assert "Beta" in out
        assert "Gamma" in out
        assert "beta" not in out
        assert "gamma" not in out


# ---------------------------------------------------------------------------
# basename / dirname
# ---------------------------------------------------------------------------


class TestBasenameDirname:
    def test_basename_basic(self, fs):
        out = execute_script(
            to_script("echo /usr/local/bin/python | xargs basename"), fs
        )
        assert out.strip() == "python"

    def test_basename_suffix(self, fs):
        out = execute_script(to_script("basename /path/to/file.txt .txt"), fs)
        assert out.strip() == "file"

    def test_dirname_basic(self, fs):
        out = execute_script(to_script("dirname /usr/local/bin/python"), fs)
        assert out.strip() == "/usr/local/bin"

    def test_dirname_no_slash(self, fs):
        out = execute_script(to_script("dirname file.txt"), fs)
        assert out.strip() == "."

    def test_dirname_root(self, fs):
        out = execute_script(to_script("dirname /file.txt"), fs)
        assert out.strip() == "/"


# ---------------------------------------------------------------------------
# && and || operators
# ---------------------------------------------------------------------------


class TestConditionalOperators:
    def test_and_both_succeed(self, fs):
        out = execute_script(to_script("echo a && echo b"), fs)
        assert out == "a\nb\n"

    def test_and_first_fails(self, fs):
        """&& skips the second command when the first fails."""
        with pytest.raises(TerminalError):
            execute_script(to_script("cd /nonexistent && echo no"), fs)

    def test_and_first_fails_no_second(self, fs):
        """The skipped command should not produce output."""
        try:
            execute_script(to_script("cd /nonexistent && echo no"), fs)
        except TerminalError as e:
            assert "no" not in (e.partial_output or "")

    def test_or_first_fails(self, fs):
        """|| runs the second command when the first fails."""
        out = execute_script(to_script("cd /nonexistent || echo fallback"), fs)
        assert out == "fallback\n"

    def test_or_first_succeeds(self, fs):
        """|| skips the second command when the first succeeds."""
        out = execute_script(to_script("echo ok || echo no"), fs)
        assert out == "ok\n"

    def test_chain_and_then_or(self, fs):
        """cmd1 && cmd2 || cmd3 — if cmd1 succeeds, cmd2 runs; if cmd2 fails, cmd3 runs."""
        out = execute_script(
            to_script("echo first && cd /nonexistent || echo recovered"), fs
        )
        assert "first" in out
        assert "recovered" in out

    def test_semicolon_always_continues(self, fs):
        """Semicolons always continue regardless of failure."""
        out = execute_script(to_script("cd /nonexistent ; echo yes"), fs)
        assert out == "yes\n"

    def test_mixed_operators(self, fs):
        out = execute_script(to_script("echo a ; echo b && echo c"), fs)
        assert out == "a\nb\nc\n"
