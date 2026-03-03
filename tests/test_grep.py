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
