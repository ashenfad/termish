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
# head -c / tail -c (byte count)
# ---------------------------------------------------------------------------


class TestHeadTailBytes:
    def test_head_bytes_from_file(self, fs):
        fs.write("/f.txt", b"abcdefghij")
        out = execute_script(to_script("head -c 5 f.txt"), fs)
        assert out == "abcde"

    def test_head_bytes_from_stdin(self, fs):
        out = execute_script(to_script("echo 'abcdefghij' | head -c 4"), fs)
        assert out == "abcd"

    def test_tail_bytes_from_file(self, fs):
        fs.write("/f.txt", b"abcdefghij")
        out = execute_script(to_script("tail -c 5 f.txt"), fs)
        assert out == "fghij"

    def test_tail_bytes_from_stdin(self, fs):
        out = execute_script(to_script("echo 'abcdefghij' | tail -c 4"), fs)
        assert out == "hij\n"

    def test_head_bytes_multiple_files(self, fs):
        fs.write("/a.txt", b"aaaa")
        fs.write("/b.txt", b"bbbb")
        out = execute_script(to_script("head -c 2 a.txt b.txt"), fs)
        assert "==> a.txt <==" in out
        assert "aa" in out
        assert "==> b.txt <==" in out
        assert "bb" in out

    def test_tail_bytes_multiple_files(self, fs):
        fs.write("/a.txt", b"aaaa")
        fs.write("/b.txt", b"bbbb")
        out = execute_script(to_script("tail -c 2 a.txt b.txt"), fs)
        assert "==> a.txt <==" in out
        assert "aa" in out
        assert "==> b.txt <==" in out
        assert "bb" in out


# ---------------------------------------------------------------------------
# ls -h / -t / -S / -r / -1
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

    def test_ls_sort_by_size(self, fs):
        fs.write("/small.txt", b"x")
        fs.write("/big.txt", b"x" * 1000)
        fs.write("/medium.txt", b"x" * 100)
        out = execute_script(to_script("ls -lS /"), fs)
        lines = [line for line in out.strip().split("\n") if line]
        big_idx = next(i for i, line in enumerate(lines) if "big.txt" in line)
        med_idx = next(i for i, line in enumerate(lines) if "medium.txt" in line)
        sml_idx = next(i for i, line in enumerate(lines) if "small.txt" in line)
        assert big_idx < med_idx < sml_idx

    def test_ls_sort_by_size_short(self, fs):
        """ls -S without -l outputs names sorted by size."""
        fs.write("/small.txt", b"x")
        fs.write("/big.txt", b"x" * 1000)
        out = execute_script(to_script("ls -S /"), fs)
        lines = out.strip().split("\n")
        big_idx = next(i for i, line in enumerate(lines) if "big.txt" in line)
        sml_idx = next(i for i, line in enumerate(lines) if "small.txt" in line)
        assert big_idx < sml_idx

    def test_ls_reverse(self, fs):
        fs.write("/a.txt", b"a")
        fs.write("/b.txt", b"b")
        # Normal order
        out_normal = execute_script(to_script("ls /"), fs)
        # Reversed
        out_rev = execute_script(to_script("ls -r /"), fs)
        normal_lines = out_normal.strip().split("\n")
        rev_lines = out_rev.strip().split("\n")
        assert normal_lines == list(reversed(rev_lines))

    def test_ls_reverse_with_sort(self, fs):
        """ls -rS reverses size sort (smallest first)."""
        fs.write("/small.txt", b"x")
        fs.write("/big.txt", b"x" * 1000)
        out = execute_script(to_script("ls -lrS /"), fs)
        lines = [line for line in out.strip().split("\n") if line]
        sml_idx = next(i for i, line in enumerate(lines) if "small.txt" in line)
        big_idx = next(i for i, line in enumerate(lines) if "big.txt" in line)
        assert sml_idx < big_idx

    def test_ls_one_per_line(self, fs):
        """-1 flag is accepted (output is already one-per-line)."""
        fs.write("/a.txt", b"a")
        fs.write("/b.txt", b"b")
        out = execute_script(to_script("ls -1 /"), fs)
        lines = out.strip().split("\n")
        assert len(lines) == 2

    def test_ls_directory_flag(self, fs):
        """ls -d lists the directory itself, not its contents."""
        fs.makedirs("/mydir")
        fs.write("/mydir/f.txt", b"")
        out = execute_script(to_script("ls -d /mydir"), fs)
        assert out.strip() == "/mydir"
        assert "f.txt" not in out

    def test_ls_directory_flag_long(self, fs):
        """ls -ld shows directory entry in long format."""
        fs.makedirs("/mydir")
        out = execute_script(to_script("ls -ld /mydir"), fs)
        assert out.startswith("d")
        assert "/mydir" in out


# ---------------------------------------------------------------------------
# diff -r / -i
# ---------------------------------------------------------------------------


class TestDiffRecursive:
    def test_identical_dirs(self, fs):
        fs.makedirs("/a")
        fs.makedirs("/b")
        fs.write("/a/f.txt", b"hello\n")
        fs.write("/b/f.txt", b"hello\n")
        out = execute_script(to_script("diff -r /a /b"), fs)
        assert out == ""

    def test_differing_file(self, fs):
        fs.makedirs("/a")
        fs.makedirs("/b")
        fs.write("/a/f.txt", b"hello\n")
        fs.write("/b/f.txt", b"world\n")
        out = execute_script(to_script("diff -r /a /b"), fs)
        assert "hello" in out
        assert "world" in out

    def test_only_in_left(self, fs):
        fs.makedirs("/a")
        fs.makedirs("/b")
        fs.write("/a/extra.txt", b"data\n")
        out = execute_script(to_script("diff -r /a /b"), fs)
        assert "Only in /a: extra.txt" in out

    def test_only_in_right(self, fs):
        fs.makedirs("/a")
        fs.makedirs("/b")
        fs.write("/b/extra.txt", b"data\n")
        out = execute_script(to_script("diff -r /a /b"), fs)
        assert "Only in /b: extra.txt" in out

    def test_nested_dirs(self, fs):
        fs.makedirs("/a/sub")
        fs.makedirs("/b/sub")
        fs.write("/a/sub/f.txt", b"one\n")
        fs.write("/b/sub/f.txt", b"two\n")
        out = execute_script(to_script("diff -r /a /b"), fs)
        assert "one" in out
        assert "two" in out

    def test_recursive_brief(self, fs):
        fs.makedirs("/a")
        fs.makedirs("/b")
        fs.write("/a/f.txt", b"hello\n")
        fs.write("/b/f.txt", b"world\n")
        out = execute_script(to_script("diff -rq /a /b"), fs)
        assert "differ" in out
        # Brief mode shouldn't show actual content
        assert "hello" not in out


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


class TestDiffContextLines:
    def test_diff_U_controls_context(self, fs):
        """diff -U N should show N lines of context."""
        lines_a = "\n".join(f"line{i}" for i in range(10)) + "\n"
        lines_b = lines_a.replace("line5", "CHANGED")
        fs.write("/a.txt", lines_a.encode())
        fs.write("/b.txt", lines_b.encode())
        # -U 1 should show only 1 line of context around the change
        out = execute_script(to_script("diff -U 1 a.txt b.txt"), fs)
        assert "line4" in out  # 1 line before change
        assert "line6" in out  # 1 line after change
        assert "line3" not in out  # 2 lines before — should be excluded
        assert "line7" not in out  # 2 lines after — should be excluded

    def test_diff_U_zero(self, fs):
        """diff -U 0 shows only changed lines with no context."""
        fs.write("/a.txt", b"same\nold\nsame\n")
        fs.write("/b.txt", b"same\nnew\nsame\n")
        out = execute_script(to_script("diff -U 0 a.txt b.txt"), fs)
        assert "old" in out
        assert "new" in out
        # "same" should only appear in header lines, not as context
        context_lines = [x for x in out.split("\n") if x.startswith(" ")]
        assert len(context_lines) == 0


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


# ---------------------------------------------------------------------------
# mv flags
# ---------------------------------------------------------------------------


class TestMvFlags:
    def test_mv_with_force(self, fs):
        """mv -f should work (force is default behavior)."""
        fs.write("/a.txt", b"hello")
        execute_script(to_script("mv -f a.txt b.txt"), fs)
        assert fs.exists("/b.txt")
        assert not fs.exists("/a.txt")

    def test_mv_no_clobber(self, fs):
        """mv -n should not overwrite existing file."""
        fs.write("/a.txt", b"original")
        fs.write("/b.txt", b"existing")
        execute_script(to_script("mv -n a.txt b.txt"), fs)
        assert fs.read("/b.txt") == b"existing"
        assert fs.exists("/a.txt")

    def test_mv_unknown_option(self, fs):
        fs.write("/a.txt", b"")
        with pytest.raises(TerminalError, match="unknown option"):
            execute_script(to_script("mv --unknown a.txt b.txt"), fs)
