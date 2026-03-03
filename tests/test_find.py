import pytest

from termish.errors import TerminalError
from termish.fs import MemoryFS
from termish.interpreter import execute_script
from termish.parser import to_script


@pytest.fixture
def fs():
    return MemoryFS()


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
