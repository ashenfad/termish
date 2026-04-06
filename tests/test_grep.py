import pytest

from termish.errors import TerminalError
from termish.fs import MemoryFS
from termish.interpreter import execute_script
from termish.parser import to_script


@pytest.fixture
def fs():
    return MemoryFS()


def test_grep_recursive(fs):
    execute_script(to_script("mkdir -p src"), fs)
    execute_script(to_script("echo 'def foo(): pass' > src/main.py"), fs)
    execute_script(to_script("echo 'class Bar: pass' > src/models.py"), fs)

    output = execute_script(to_script("grep -r 'def' src"), fs)
    assert "src/main.py:def foo(): pass" in output
    assert "src/models.py" not in output


def test_grep_auto_recurse_directory(fs):
    """grep on a directory without -r should auto-recurse."""
    execute_script(to_script("mkdir -p src"), fs)
    execute_script(to_script("echo 'def foo(): pass' > src/main.py"), fs)
    execute_script(to_script("echo 'class Bar: pass' > src/models.py"), fs)

    output = execute_script(to_script("grep 'def' src"), fs)
    assert "src/main.py:def foo(): pass" in output
    assert "src/models.py" not in output


def test_grep_recursive_preserves_relative_paths(fs):
    """grep -r should preserve the user's relative path prefix in output."""
    execute_script(to_script("mkdir -p chapters/data/events"), fs)
    execute_script(to_script("echo 'task started' > chapters/data/events/001.md"), fs)
    execute_script(to_script("echo 'no match' > chapters/data/events/002.md"), fs)

    output = execute_script(to_script("grep -r 'task' chapters/"), fs)
    assert "chapters/data/events/001.md" in output
    # Should NOT contain absolute paths
    assert output.strip().startswith("chapters/")


def test_grep_recursive_deep_nesting(fs):
    """grep -r with deeply nested relative paths should preserve them."""
    fs.makedirs("/project/src/lib")
    fs.write("/project/src/lib/util.py", b"hello world")
    fs.chdir("/project")
    output = execute_script(to_script("grep -r 'hello' src"), fs)
    assert "src/lib/util.py" in output


def test_grep_file_simple(fs):
    execute_script(to_script("echo 'hello' > hello.txt"), fs)
    output = execute_script(to_script("grep 'hello' hello.txt"), fs)
    assert "hello" in output


def test_grep_context_after(fs):
    """Test grep -A (after context)."""
    content = "line1\nmatch\nline3\nline4\nline5"
    execute_script(to_script(f"echo '{content}' > test.txt"), fs)

    output = execute_script(to_script("grep -A 2 'match' test.txt"), fs)
    lines = output.strip().split("\n")
    assert "match" in lines[0]
    assert "line3" in lines[1]
    assert "line4" in lines[2]


def test_grep_context_before(fs):
    """Test grep -B (before context)."""
    content = "line1\nline2\nmatch\nline4"
    execute_script(to_script(f"echo '{content}' > test.txt"), fs)

    output = execute_script(to_script("grep -B 2 'match' test.txt"), fs)
    lines = output.strip().split("\n")
    assert "line1" in lines[0]
    assert "line2" in lines[1]
    assert "match" in lines[2]


def test_grep_context_both(fs):
    """Test grep -C (before and after context)."""
    content = "line1\nline2\nmatch\nline4\nline5"
    execute_script(to_script(f"echo '{content}' > test.txt"), fs)

    output = execute_script(to_script("grep -C 1 'match' test.txt"), fs)
    lines = output.strip().split("\n")
    assert "line2" in lines[0]
    assert "match" in lines[1]
    assert "line4" in lines[2]


def test_grep_context_separator(fs):
    """Test grep context separator between non-adjacent matches."""
    content = "line1\nmatch1\nline3\nline4\nline5\nmatch2\nline7"
    execute_script(to_script(f"echo '{content}' > test.txt"), fs)

    output = execute_script(to_script("grep -C 1 'match' test.txt"), fs)
    assert "--" in output


def test_grep_unknown_option(fs):
    """Test that grep errors on unknown options."""
    execute_script(to_script("echo 'test' > test.txt"), fs)

    with pytest.raises(TerminalError) as excinfo:
        execute_script(to_script("grep --unknown test test.txt"), fs)
    assert "unknown option" in str(excinfo.value)


def test_grep_file_not_found(fs):
    """Test that grep gives clear error for missing files."""
    with pytest.raises(TerminalError) as excinfo:
        execute_script(to_script("grep 'test' nonexistent.txt"), fs)
    assert "No such file or directory" in str(excinfo.value)


# ---------------------------------------------------------------------------
# grep -c / -w / -o / --include / --exclude
# ---------------------------------------------------------------------------


class TestGrepFlags:
    def test_grep_count(self, fs):
        fs.write("/f.txt", b"apple\nbanana\napricot\n")
        out = execute_script(to_script("grep -c '^a' f.txt"), fs)
        assert out.strip() == "2"

    def test_grep_word(self, fs):
        fs.write("/f.txt", b"cat\ncatalog\nthe cat sat\n")
        out = execute_script(to_script("grep -w 'cat' f.txt"), fs)
        assert "cat\n" in out
        assert "the cat sat\n" in out
        assert "catalog" not in out

    def test_grep_only_matching(self, fs):
        fs.write("/f.txt", b"abc 123 def 456\n")
        out = execute_script(to_script("grep -o '[0-9]+' f.txt"), fs)
        assert "123\n" in out
        assert "456\n" in out

    def test_grep_include(self, fs):
        fs.mkdir("/src")
        fs.write("/src/a.py", b"TODO: fix\n")
        fs.write("/src/a.txt", b"TODO: fix\n")
        out = execute_script(to_script("grep -r --include '*.py' TODO /src"), fs)
        assert "a.py" in out
        assert "a.txt" not in out

    def test_grep_exclude(self, fs):
        fs.mkdir("/src")
        fs.write("/src/a.py", b"TODO: fix\n")
        fs.write("/src/a.txt", b"TODO: fix\n")
        out = execute_script(to_script("grep -r --exclude '*.txt' TODO /src"), fs)
        assert "a.py" in out
        assert "a.txt" not in out


# ---------------------------------------------------------------------------
# grep -e (multiple patterns)
# ---------------------------------------------------------------------------


class TestGrepMultiPattern:
    def test_single_e_flag(self, fs):
        fs.write("/f.txt", b"apple\nbanana\ncherry\n")
        out = execute_script(to_script("grep -e 'apple' f.txt"), fs)
        assert "apple" in out
        assert "banana" not in out

    def test_two_e_flags(self, fs):
        fs.write("/f.txt", b"apple\nbanana\ncherry\n")
        out = execute_script(to_script("grep -e 'apple' -e 'cherry' f.txt"), fs)
        assert "apple" in out
        assert "cherry" in out
        assert "banana" not in out

    def test_three_e_flags(self, fs):
        fs.write("/f.txt", b"one\ntwo\nthree\nfour\n")
        out = execute_script(to_script("grep -e one -e two -e three f.txt"), fs)
        lines = out.strip().split("\n")
        assert len(lines) == 3
        assert "four" not in out

    def test_e_with_count(self, fs):
        fs.write("/f.txt", b"apple\nbanana\napricot\nblueberry\n")
        out = execute_script(to_script("grep -c -e 'apple' -e 'banana' f.txt"), fs)
        assert out.strip() == "2"

    def test_e_with_invert(self, fs):
        fs.write("/f.txt", b"apple\nbanana\ncherry\n")
        out = execute_script(to_script("grep -v -e 'apple' -e 'cherry' f.txt"), fs)
        assert out.strip() == "banana"

    def test_e_with_fixed_strings(self, fs):
        fs.write("/f.txt", b"a.b\nc.d\ne.f\n")
        out = execute_script(to_script("grep -F -e 'a.b' -e 'e.f' f.txt"), fs)
        assert "a.b" in out
        assert "e.f" in out
        assert "c.d" not in out

    def test_e_from_stdin(self, fs):
        out = execute_script(
            to_script("echo 'apple\nbanana\ncherry' | grep -e apple -e cherry"), fs
        )
        assert "apple" in out
        assert "cherry" in out
        assert "banana" not in out

    def test_no_pattern_errors(self, fs):
        """grep with no args at all should error."""
        with pytest.raises(TerminalError, match="no pattern"):
            execute_script(to_script("echo test | grep"), fs)


# ---------------------------------------------------------------------------
# grep -m (max count)
# ---------------------------------------------------------------------------


class TestGrepMaxCount:
    def test_max_count_limits_output(self, fs):
        fs.write("/f.txt", b"aaa\nbbb\naaa\naaa\n")
        out = execute_script(to_script("grep -m 2 aaa f.txt"), fs)
        assert out.strip().split("\n") == ["aaa", "aaa"]

    def test_max_count_one(self, fs):
        fs.write("/f.txt", b"one\ntwo\none\n")
        out = execute_script(to_script("grep -m 1 one f.txt"), fs)
        assert out == "one\n"

    def test_max_count_from_stdin(self, fs):
        out = execute_script(to_script("echo 'a\nb\na\na' | grep -m 2 a"), fs)
        lines = out.strip().split("\n")
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# grep --exclude-dir
# ---------------------------------------------------------------------------


class TestGrepExcludeDir:
    def test_exclude_dir(self, fs):
        fs.makedirs("/src/.git/objects")
        fs.write("/src/.git/objects/abc", b"TODO fix\n")
        fs.write("/src/main.py", b"TODO fix\n")
        out = execute_script(to_script("grep -r --exclude-dir '.git' TODO /src"), fs)
        assert "main.py" in out
        assert ".git" not in out

    def test_exclude_dir_pattern(self, fs):
        fs.makedirs("/project/node_modules/pkg")
        fs.write("/project/node_modules/pkg/index.js", b"hello\n")
        fs.write("/project/app.js", b"hello\n")
        out = execute_script(
            to_script("grep -r --exclude-dir 'node_modules' hello /project"), fs
        )
        assert "app.js" in out
        assert "node_modules" not in out


# ---------------------------------------------------------------------------
# grep -q (quiet)
# ---------------------------------------------------------------------------


class TestGrepQuiet:
    def test_quiet_no_output(self, fs):
        fs.write("/f.txt", b"hello world\n")
        out = execute_script(to_script("grep -q hello f.txt"), fs)
        assert out == ""

    def test_quiet_with_conditional(self, fs):
        """grep -q pattern && echo found — should succeed without output."""
        fs.write("/f.txt", b"hello\n")
        out = execute_script(to_script("grep -q hello f.txt && echo found"), fs)
        assert out == "found\n"

    def test_quiet_no_match_still_silent(self, fs):
        """grep -q with no match should still produce no output."""
        fs.write("/f.txt", b"hello\n")
        out = execute_script(to_script("grep -q nope f.txt"), fs)
        assert out == ""


# ---------------------------------------------------------------------------
# grep -L (files without matches)
# ---------------------------------------------------------------------------


class TestGrepFilesWithoutMatch:
    def test_files_without_match(self, fs):
        fs.write("/a.txt", b"hello\n")
        fs.write("/b.txt", b"world\n")
        out = execute_script(to_script("grep -L hello a.txt b.txt"), fs)
        assert "b.txt" in out
        assert "a.txt" not in out

    def test_files_without_match_all_match(self, fs):
        fs.write("/a.txt", b"hello\n")
        fs.write("/b.txt", b"hello world\n")
        out = execute_script(to_script("grep -L hello a.txt b.txt"), fs)
        assert out == ""

    def test_files_without_match_none_match(self, fs):
        fs.write("/a.txt", b"foo\n")
        fs.write("/b.txt", b"bar\n")
        out = execute_script(to_script("grep -L hello a.txt b.txt"), fs)
        assert "a.txt" in out
        assert "b.txt" in out


# ---------------------------------------------------------------------------
# grep -H / -h (filename control)
# ---------------------------------------------------------------------------


class TestGrepFilenameControl:
    def test_with_filename_single_file(self, fs):
        fs.write("/f.txt", b"hello\n")
        out = execute_script(to_script("grep -H hello f.txt"), fs)
        assert out == "f.txt:hello\n"

    def test_no_filename_multiple_files(self, fs):
        fs.write("/a.txt", b"hello\n")
        fs.write("/b.txt", b"hello\n")
        out = execute_script(to_script("grep -h hello a.txt b.txt"), fs)
        assert out == "hello\nhello\n"


# ---------------------------------------------------------------------------
# grep BRE alternation (\|)
# ---------------------------------------------------------------------------


class TestGrepBREAlternation:
    """BRE-style \\| alternation should work (converted to ERE | internally)."""

    def test_backslash_pipe_alternation(self, fs):
        fs.write("/f.txt", b"apple\nbanana\ncherry\n")
        out = execute_script(to_script(r"grep 'apple\|cherry' f.txt"), fs)
        assert "apple" in out
        assert "cherry" in out
        assert "banana" not in out

    def test_backslash_pipe_with_count(self, fs):
        fs.write("/f.txt", b"apple\nbanana\ncherry\n")
        out = execute_script(to_script(r"grep -c 'apple\|cherry' f.txt"), fs)
        assert out.strip() == "2"

    def test_ere_pipe_still_works(self, fs):
        fs.write("/f.txt", b"apple\nbanana\ncherry\n")
        out = execute_script(to_script("grep -E 'apple|cherry' f.txt"), fs)
        assert "apple" in out
        assert "cherry" in out

    def test_ere_pipe_without_flag(self, fs):
        """Bare | (ERE style) should also work since Python re is ERE-like."""
        fs.write("/f.txt", b"apple\nbanana\ncherry\n")
        out = execute_script(to_script("grep 'apple|cherry' f.txt"), fs)
        assert "apple" in out
        assert "cherry" in out

    def test_fixed_strings_no_conversion(self, fs):
        """With -F, \\| should be treated literally (no alternation)."""
        fs.write("/f.txt", b"a\\|b\nhello\n")
        out = execute_script(to_script(r"grep -F 'a\|b' f.txt"), fs)
        assert "a\\|b" in out
        assert "hello" not in out
