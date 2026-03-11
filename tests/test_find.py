import pytest

from termish.errors import TerminalError
from termish.fs import MemoryFS
from termish.interpreter import execute_script
from termish.parser import to_script


@pytest.fixture
def fs():
    return MemoryFS()


def test_find_preserves_relative_paths(fs):
    """find should preserve the user's relative path prefix in output."""
    execute_script(to_script("mkdir -p chapters/data/events"), fs)
    execute_script(to_script("echo 'x' > chapters/data/events/001.md"), fs)

    output = execute_script(to_script("find chapters/ -name '*.md'"), fs)
    assert "chapters/data/events/001.md" in output
    # Should NOT contain absolute paths
    for line in output.strip().splitlines():
        assert line.startswith("chapters/"), f"Expected relative path, got: {line}"


def test_find_unknown_option(fs):
    """Test that find errors on unknown predicates."""
    with pytest.raises(TerminalError) as excinfo:
        execute_script(to_script("find . --unknown"), fs)
    assert "unknown predicate" in str(excinfo.value)


# ---------------------------------------------------------------------------
# find -maxdepth / -mindepth
# ---------------------------------------------------------------------------


class TestFindDepth:
    def test_find_maxdepth(self, fs):
        fs.makedirs("/a/b/c")
        fs.write("/a/x.txt", b"")
        fs.write("/a/b/y.txt", b"")
        fs.write("/a/b/c/z.txt", b"")
        out = execute_script(to_script("find /a -maxdepth 1 -type f"), fs)
        assert "x.txt" in out
        assert "y.txt" not in out

    def test_find_mindepth(self, fs):
        fs.makedirs("/a/b")
        fs.write("/a/x.txt", b"")
        fs.write("/a/b/y.txt", b"")
        out = execute_script(to_script("find /a -mindepth 2 -type f"), fs)
        assert "y.txt" in out
        assert "x.txt" not in out


# ---------------------------------------------------------------------------
# find — compound predicates (-o, -not, parentheses)
# ---------------------------------------------------------------------------


class TestFindPredicates:
    def test_or_two_names(self, fs):
        fs.write("/a.md", b"")
        fs.write("/b.txt", b"")
        fs.write("/c.py", b"")
        out = execute_script(to_script("find / -name '*.md' -o -name '*.txt'"), fs)
        assert "a.md" in out
        assert "b.txt" in out
        assert "c.py" not in out

    def test_implicit_and(self, fs):
        fs.makedirs("/d")
        fs.write("/f.py", b"")
        out = execute_script(to_script("find / -type f -name '*.py'"), fs)
        assert "f.py" in out
        assert "/d" not in out

    def test_explicit_and(self, fs):
        fs.makedirs("/d")
        fs.write("/f.py", b"")
        out = execute_script(to_script("find / -type f -and -name '*.py'"), fs)
        assert "f.py" in out
        assert "/d" not in out

    def test_not_predicate(self, fs):
        fs.write("/a.py", b"")
        fs.write("/b.txt", b"")
        out = execute_script(to_script("find / -type f -not -name '*.py'"), fs)
        assert "b.txt" in out
        assert "a.py" not in out

    def test_not_with_bang(self, fs):
        fs.write("/a.py", b"")
        fs.write("/b.txt", b"")
        out = execute_script(to_script("find / -type f ! -name '*.py'"), fs)
        assert "b.txt" in out
        assert "a.py" not in out

    def test_parenthesized_or(self, fs):
        """find /path \\( -name '*.md' -o -name '*.txt' \\) -type f"""
        fs.makedirs("/sub")
        fs.write("/a.md", b"")
        fs.write("/b.txt", b"")
        fs.write("/c.py", b"")
        out = execute_script(
            to_script("find / '(' -name '*.md' -o -name '*.txt' ')' -type f"),
            fs,
        )
        assert "a.md" in out
        assert "b.txt" in out
        assert "c.py" not in out
        assert "/sub" not in out

    def test_or_precedence_lower_than_and(self, fs):
        """Without parens: -type f -name '*.md' -o -name '*.txt'
        means (-type f AND -name '*.md') OR (-name '*.txt')
        so directories named *.txt would match."""
        fs.makedirs("/docs.txt")
        fs.write("/a.md", b"")
        fs.write("/b.txt", b"")
        out = execute_script(
            to_script("find / -type f -name '*.md' -o -name '*.txt'"), fs
        )
        assert "a.md" in out
        # b.txt matches the -name '*.txt' OR branch
        assert "b.txt" in out
        # docs.txt dir also matches the -name '*.txt' OR branch
        assert "docs.txt" in out

    def test_no_predicates_matches_all(self, fs):
        fs.makedirs("/d")
        fs.write("/f.txt", b"")
        out = execute_script(to_script("find /"), fs)
        assert "d" in out
        assert "f.txt" in out

    def test_or_with_maxdepth(self, fs):
        """Global options work alongside compound predicates."""
        fs.makedirs("/a/b")
        fs.write("/a/x.md", b"")
        fs.write("/a/y.txt", b"")
        fs.write("/a/b/z.md", b"")
        out = execute_script(
            to_script("find /a -maxdepth 1 '(' -name '*.md' -o -name '*.txt' ')'"),
            fs,
        )
        assert "x.md" in out
        assert "y.txt" in out
        assert "z.md" not in out

    def test_missing_closing_paren(self, fs):
        with pytest.raises(TerminalError, match="missing closing"):
            execute_script(to_script("find / '(' -name '*.md'"), fs)

    def test_name_missing_pattern(self, fs):
        with pytest.raises(TerminalError, match="-name requires"):
            execute_script(to_script("find / -name"), fs)

    def test_type_invalid_value(self, fs):
        with pytest.raises(TerminalError, match="unknown type"):
            execute_script(to_script("find / -type x"), fs)


# ---------------------------------------------------------------------------
# find -size
# ---------------------------------------------------------------------------


class TestFindSize:
    def test_size_greater_than(self, fs):
        fs.write("/small.txt", b"x")
        fs.write("/big.txt", b"x" * 2000)
        out = execute_script(to_script("find / -size +1k -type f"), fs)
        assert "big.txt" in out
        assert "small.txt" not in out

    def test_size_less_than(self, fs):
        fs.write("/small.txt", b"x")
        fs.write("/big.txt", b"x" * 2000)
        out = execute_script(to_script("find / -size -1k -type f"), fs)
        assert "small.txt" in out
        assert "big.txt" not in out

    def test_size_exact_bytes(self, fs):
        fs.write("/f.txt", b"hello")  # 5 bytes
        out = execute_script(to_script("find / -size 5c -type f"), fs)
        assert "f.txt" in out

    def test_size_megabytes(self, fs):
        fs.write("/small.txt", b"x")
        out = execute_script(to_script("find / -size +1M -type f"), fs)
        assert "small.txt" not in out

    def test_size_with_or(self, fs):
        """Compound: -size works with -o."""
        fs.write("/tiny.txt", b"x")
        fs.write("/big.txt", b"x" * 2000)
        fs.write("/med.txt", b"x" * 500)
        out = execute_script(
            to_script("find / -type f '(' -size +1k -o -size 1c ')'"), fs
        )
        assert "big.txt" in out
        assert "tiny.txt" in out
        assert "med.txt" not in out

    def test_size_missing_arg(self, fs):
        with pytest.raises(TerminalError, match="-size requires"):
            execute_script(to_script("find / -size"), fs)


# ---------------------------------------------------------------------------
# find -exec
# ---------------------------------------------------------------------------


class TestFindExec:
    def test_exec_grep(self, fs):
        """find -exec grep pattern {} \\;"""
        fs.write("/a.py", b"def hello():\n    pass\n")
        fs.write("/b.py", b"class Foo:\n    pass\n")
        out = execute_script(
            to_script("find / -name '*.py' -exec grep -l def '{}' ';'"), fs
        )
        assert "a.py" in out
        assert "b.py" not in out

    def test_exec_cat(self, fs):
        fs.write("/f.txt", b"content here\n")
        out = execute_script(to_script("find / -name 'f.txt' -exec cat '{}' ';'"), fs)
        assert "content here" in out

    def test_exec_with_type_filter(self, fs):
        """Combine -type f with -exec."""
        fs.makedirs("/d")
        fs.write("/f.txt", b"data\n")
        out = execute_script(to_script("find / -type f -exec cat '{}' ';'"), fs)
        assert "data" in out

    def test_exec_missing_semicolon(self, fs):
        with pytest.raises(TerminalError, match="terminating"):
            execute_script(to_script("find / -exec echo '{}' "), fs)

    def test_exec_empty_command(self, fs):
        with pytest.raises(TerminalError, match="requires a command"):
            execute_script(to_script("find / -exec ';'"), fs)


# ---------------------------------------------------------------------------
# find -iname
# ---------------------------------------------------------------------------


class TestFindIName:
    def test_iname_matches_case_insensitive(self, fs):
        fs.write("/README.MD", b"")
        fs.write("/notes.md", b"")
        fs.write("/code.py", b"")
        out = execute_script(to_script("find / -iname '*.md'"), fs)
        assert "README.MD" in out
        assert "notes.md" in out
        assert "code.py" not in out

    def test_iname_with_type(self, fs):
        fs.makedirs("/Docs")
        fs.write("/docs.txt", b"")
        out = execute_script(to_script("find / -iname 'docs*' -type f"), fs)
        assert "docs.txt" in out
        assert "/Docs" not in out


# ---------------------------------------------------------------------------
# find -print
# ---------------------------------------------------------------------------


class TestFindPrint:
    def test_print_explicit(self, fs):
        fs.write("/a.txt", b"")
        fs.write("/b.py", b"")
        out = execute_script(to_script("find / -name '*.txt' -print"), fs)
        assert "a.txt" in out
        assert "b.py" not in out

    def test_print_with_exec_suppresses_default(self, fs):
        """When -print and -exec both exist, only explicit actions run."""
        fs.write("/f.txt", b"hello\n")
        out = execute_script(
            to_script("find / -name 'f.txt' -exec cat '{}' ';'"),
            fs,
        )
        # -exec suppresses default print, so only cat output appears
        assert "hello" in out

    def test_print_combined_with_or(self, fs):
        """find / -name '*.md' -print -o -name '*.txt' -print"""
        fs.write("/a.md", b"")
        fs.write("/b.txt", b"")
        fs.write("/c.py", b"")
        out = execute_script(
            to_script("find / -name '*.md' -print -o -name '*.txt' -print"),
            fs,
        )
        assert "a.md" in out
        assert "b.txt" in out
        assert "c.py" not in out


# ---------------------------------------------------------------------------
# find -path
# ---------------------------------------------------------------------------


class TestFindPath:
    def test_path_match(self, fs):
        fs.makedirs("/src/test")
        fs.write("/src/test/test_foo.py", b"")
        fs.write("/src/main.py", b"")
        out = execute_script(to_script("find / -path '*/test/*'"), fs)
        assert "test_foo.py" in out
        assert "main.py" not in out

    def test_path_with_type(self, fs):
        fs.makedirs("/a/b")
        fs.write("/a/b/f.txt", b"")
        fs.write("/a/g.txt", b"")
        out = execute_script(to_script("find / -path '*/b/*' -type f"), fs)
        assert "f.txt" in out
        assert "g.txt" not in out


# ---------------------------------------------------------------------------
# find -delete
# ---------------------------------------------------------------------------


class TestFindDelete:
    def test_delete_files(self, fs):
        fs.write("/a.tmp", b"")
        fs.write("/b.txt", b"")
        execute_script(to_script("find / -name '*.tmp' -delete"), fs)
        assert not fs.exists("/a.tmp")
        assert fs.exists("/b.txt")

    def test_delete_suppresses_output(self, fs):
        fs.write("/a.tmp", b"")
        out = execute_script(to_script("find / -name '*.tmp' -delete"), fs)
        # -delete is an action, so default printing is suppressed
        assert out == ""

    def test_delete_empty_dir(self, fs):
        fs.makedirs("/empty")
        fs.write("/keep.txt", b"")
        execute_script(to_script("find / -type d -name 'empty' -delete"), fs)
        assert not fs.exists("/empty")
        assert fs.exists("/keep.txt")


# ---------------------------------------------------------------------------
# find -empty
# ---------------------------------------------------------------------------


class TestFindEmpty:
    def test_empty_file(self, fs):
        fs.write("/empty.txt", b"")
        fs.write("/notempty.txt", b"data")
        out = execute_script(to_script("find / -empty -type f"), fs)
        assert "empty.txt" in out
        assert "notempty.txt" not in out

    def test_empty_dir(self, fs):
        fs.makedirs("/emptydir")
        fs.makedirs("/fulldir")
        fs.write("/fulldir/f.txt", b"")
        out = execute_script(to_script("find / -empty -type d"), fs)
        assert "emptydir" in out
        assert "fulldir" not in out

    def test_empty_combined_with_delete(self, fs):
        fs.write("/a.tmp", b"")
        fs.write("/b.txt", b"data")
        execute_script(to_script("find / -empty -type f -delete"), fs)
        assert not fs.exists("/a.tmp")
        assert fs.exists("/b.txt")
