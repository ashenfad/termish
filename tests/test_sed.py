import pytest

from termish.errors import TerminalError
from termish.fs import MemoryFS
from termish.interpreter import execute_script
from termish.parser import to_script


@pytest.fixture
def fs():
    return MemoryFS()


# ---------------------------------------------------------------------------
# Substitution
# ---------------------------------------------------------------------------


class TestSedSubstitution:
    def test_basic_substitution(self, fs):
        execute_script(to_script("echo 'hello world hello' > f.txt"), fs)
        out = execute_script(to_script("sed 's/hello/hi/' f.txt"), fs)
        assert out == "hi world hello\n"

    def test_global_substitution(self, fs):
        execute_script(to_script("echo 'hello world hello' > f.txt"), fs)
        out = execute_script(to_script("sed 's/hello/hi/g' f.txt"), fs)
        assert out == "hi world hi\n"

    def test_case_insensitive(self, fs):
        execute_script(to_script("echo 'Hello HELLO hello' > f.txt"), fs)
        out = execute_script(to_script("sed 's/hello/hi/gi' f.txt"), fs)
        assert out == "hi hi hi\n"

    def test_empty_replacement(self, fs):
        execute_script(to_script("echo 'foobarfoo' > f.txt"), fs)
        out = execute_script(to_script("sed 's/foo//g' f.txt"), fs)
        assert out == "bar\n"

    def test_regex_metacharacters(self, fs):
        execute_script(to_script("echo 'abc123def456' > f.txt"), fs)
        out = execute_script(to_script("sed 's/[0-9]+/NUM/g' f.txt"), fs)
        assert out == "abcNUMdefNUM\n"

    def test_replacement_with_ampersand(self, fs):
        execute_script(to_script("echo 'foo bar' > f.txt"), fs)
        out = execute_script(to_script("sed 's/foo/(&)/g' f.txt"), fs)
        assert out == "(foo) bar\n"

    def test_replacement_with_backreference(self, fs):
        execute_script(to_script("echo 'hello world' > f.txt"), fs)
        out = execute_script(to_script("sed 's/(hello) (world)/\\2 \\1/' f.txt"), fs)
        assert out == "world hello\n"

    def test_multiline_substitution(self, fs):
        fs.write("/f.txt", b"line1 old\nline2 old\nline3 old\n")
        out = execute_script(to_script("sed 's/old/new/g' f.txt"), fs)
        assert out == "line1 new\nline2 new\nline3 new\n"


# ---------------------------------------------------------------------------
# Delimiters
# ---------------------------------------------------------------------------


class TestSedDelimiters:
    def test_pipe_delimiter(self, fs):
        execute_script(to_script("echo '/usr/local/bin' > f.txt"), fs)
        out = execute_script(to_script("sed 's|/usr/local|/opt|' f.txt"), fs)
        assert out == "/opt/bin\n"

    def test_hash_delimiter(self, fs):
        execute_script(to_script("echo 'old text' > f.txt"), fs)
        out = execute_script(to_script("sed 's#old#new#g' f.txt"), fs)
        assert out == "new text\n"

    def test_at_delimiter(self, fs):
        execute_script(to_script("echo 'old text' > f.txt"), fs)
        out = execute_script(to_script("sed 's@old@new@g' f.txt"), fs)
        assert out == "new text\n"


# ---------------------------------------------------------------------------
# Line printing (-n + p)
# ---------------------------------------------------------------------------


class TestSedLinePrinting:
    def test_print_line_range(self, fs):
        fs.write("/f.txt", b"a\nb\nc\nd\ne\n")
        out = execute_script(to_script("sed -n '2,4p' f.txt"), fs)
        assert out == "b\nc\nd\n"

    def test_print_single_line(self, fs):
        fs.write("/f.txt", b"a\nb\nc\n")
        out = execute_script(to_script("sed -n '2p' f.txt"), fs)
        assert out == "b\n"

    def test_print_last_line(self, fs):
        fs.write("/f.txt", b"a\nb\nc\n")
        out = execute_script(to_script("sed -n '$p' f.txt"), fs)
        assert out == "c\n"

    def test_print_range_to_end(self, fs):
        fs.write("/f.txt", b"a\nb\nc\nd\ne\n")
        out = execute_script(to_script("sed -n '3,$p' f.txt"), fs)
        assert out == "c\nd\ne\n"

    def test_print_regex_address(self, fs):
        fs.write("/f.txt", b"apple\nbanana\navocado\nblueberry\n")
        out = execute_script(to_script("sed -n '/^a/p' f.txt"), fs)
        assert out == "apple\navocado\n"


# ---------------------------------------------------------------------------
# Addressing
# ---------------------------------------------------------------------------


class TestSedAddressing:
    def test_single_line_substitution(self, fs):
        fs.write("/f.txt", b"aaa\nbbb\nccc\n")
        out = execute_script(to_script("sed '2s/bbb/BBB/' f.txt"), fs)
        assert out == "aaa\nBBB\nccc\n"

    def test_range_substitution(self, fs):
        fs.write("/f.txt", b"old\nold\nold\nold\nold\n")
        out = execute_script(to_script("sed '2,4s/old/new/' f.txt"), fs)
        assert out == "old\nnew\nnew\nnew\nold\n"

    def test_regex_address_substitution(self, fs):
        fs.write("/f.txt", b"# comment\ncode\n# another comment\nmore code\n")
        out = execute_script(to_script("sed '/^#/s/#/\\/\\//' f.txt"), fs)
        assert out == "// comment\ncode\n// another comment\nmore code\n"

    def test_last_line_substitution(self, fs):
        fs.write("/f.txt", b"aaa\nbbb\nccc\n")
        out = execute_script(to_script("sed '$s/ccc/CCC/' f.txt"), fs)
        assert out == "aaa\nbbb\nCCC\n"

    def test_regex_range(self, fs):
        fs.write("/f.txt", b"before\nSTART\nmiddle\nEND\nafter\n")
        out = execute_script(to_script("sed -n '/START/,/END/p' f.txt"), fs)
        assert out == "START\nmiddle\nEND\n"


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestSedDelete:
    def test_delete_line(self, fs):
        fs.write("/f.txt", b"a\nb\nc\n")
        out = execute_script(to_script("sed '2d' f.txt"), fs)
        assert out == "a\nc\n"

    def test_delete_range(self, fs):
        fs.write("/f.txt", b"a\nb\nc\nd\ne\n")
        out = execute_script(to_script("sed '2,4d' f.txt"), fs)
        assert out == "a\ne\n"

    def test_delete_pattern(self, fs):
        fs.write("/f.txt", b"keep\n\nkeep\n\nkeep\n")
        out = execute_script(to_script("sed '/^$/d' f.txt"), fs)
        assert out == "keep\nkeep\nkeep\n"

    def test_delete_last_line(self, fs):
        fs.write("/f.txt", b"a\nb\nc\n")
        out = execute_script(to_script("sed '$d' f.txt"), fs)
        assert out == "a\nb\n"


# ---------------------------------------------------------------------------
# In-place editing (-i)
# ---------------------------------------------------------------------------


class TestSedInPlace:
    def test_in_place_basic(self, fs):
        fs.write("/f.txt", b"hello world\n")
        out = execute_script(to_script("sed -i 's/world/earth/' f.txt"), fs)
        assert out == ""  # no stdout
        assert fs.read("/f.txt") == b"hello earth\n"

    def test_in_place_multiple_files(self, fs):
        fs.write("/a.txt", b"old\n")
        fs.write("/b.txt", b"old\n")
        execute_script(to_script("sed -i 's/old/new/g' a.txt b.txt"), fs)
        assert fs.read("/a.txt") == b"new\n"
        assert fs.read("/b.txt") == b"new\n"

    def test_in_place_without_files_errors(self, fs):
        with pytest.raises(TerminalError) as exc:
            execute_script(to_script("sed -i 's/a/b/'"), fs)
        assert "-i requires" in str(exc.value)


# ---------------------------------------------------------------------------
# Multiple expressions
# ---------------------------------------------------------------------------


class TestSedMultipleExpressions:
    def test_multiple_e_flags(self, fs):
        fs.write("/f.txt", b"foo baz\n")
        out = execute_script(
            to_script("sed -e 's/foo/bar/g' -e 's/baz/qux/g' f.txt"), fs
        )
        assert out == "bar qux\n"

    def test_semicolon_separated(self, fs):
        fs.write("/f.txt", b"a\nb\nc\nd\n")
        out = execute_script(to_script("sed '2d;4d' f.txt"), fs)
        # line 2 (b) deleted; after deletion, original line 4 (d) is now
        # still line 4 in the original numbering
        assert out == "a\nc\n"

    def test_semicolon_in_replacement(self, fs):
        """Semicolons inside s/// replacement must not split the command."""
        fs.write("/f.txt", b"hello\n")
        out = execute_script(to_script("sed 's/hello/foo;bar/' f.txt"), fs)
        assert out == "foo;bar\n"

    def test_semicolon_in_pattern(self, fs):
        """Semicolons inside s/// pattern must not split the command."""
        fs.write("/f.txt", b"foo;bar\n")
        out = execute_script(to_script("sed 's/foo;bar/baz/' f.txt"), fs)
        assert out == "baz\n"

    def test_semicolon_in_replacement_with_following_command(self, fs):
        """Semicolon in replacement followed by a real semicolon-separated cmd."""
        fs.write("/f.txt", b"a\nb\n")
        out = execute_script(to_script("sed 's/a/x;y/;2d' f.txt"), fs)
        assert out == "x;y\n"


# ---------------------------------------------------------------------------
# Stdin
# ---------------------------------------------------------------------------


class TestSedStdin:
    def test_stdin_substitution(self, fs):
        out = execute_script(to_script("echo 'hello world' | sed 's/world/earth/'"), fs)
        assert out == "hello earth\n"

    def test_stdin_pipeline(self, fs):
        fs.write("/f.txt", b"a\nb\nc\nd\ne\n")
        out = execute_script(to_script("cat f.txt | sed -n '1,3p'"), fs)
        assert out == "a\nb\nc\n"


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


class TestSedIntegration:
    def test_grep_sed_pipeline(self, fs):
        fs.write("/f.txt", b"error: bad input\ninfo: ok\nerror: timeout\n")
        out = execute_script(
            to_script("grep 'error' f.txt | sed 's/error/ERROR/g'"), fs
        )
        assert out == "ERROR: bad input\nERROR: timeout\n"

    def test_find_xargs_sed(self, fs):
        fs.mkdir("/src")
        fs.write("/src/a.py", b"import foo\nfoo.bar()\n")
        fs.write("/src/b.py", b"import foo\nfoo.baz()\n")
        # xargs passes all file paths as args to a single sed invocation
        out = execute_script(
            to_script("find /src -name '*.py' | xargs sed 's/foo/pkg/g'"), fs
        )
        assert "import pkg" in out
        assert "pkg.bar()" in out
        assert "pkg.baz()" in out

    def test_find_xargs_sed_in_place(self, fs):
        """The primary agent use case: bulk in-place replacement across files."""
        fs.mkdir("/src")
        fs.write("/src/a.py", b"import foo\nfoo.bar()\n")
        fs.write("/src/b.py", b"import foo\nfoo.baz()\n")
        execute_script(
            to_script("find /src -name '*.py' | xargs sed -i 's/foo/pkg/g'"), fs
        )
        assert fs.read("/src/a.py") == b"import pkg\npkg.bar()\n"
        assert fs.read("/src/b.py") == b"import pkg\npkg.baz()\n"

    def test_sed_with_head(self, fs):
        fs.write("/f.txt", b"a\nb\nc\nd\ne\n")
        out = execute_script(to_script("sed -n '1,3p' f.txt | head -n 2"), fs)
        assert out == "a\nb\n"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TestSedErrors:
    def test_no_expression(self, fs):
        with pytest.raises(TerminalError) as exc:
            execute_script(to_script("sed"), fs)
        assert "no expression" in str(exc.value)

    def test_invalid_regex(self, fs):
        fs.write("/f.txt", b"test\n")
        with pytest.raises(TerminalError) as exc:
            execute_script(to_script("sed 's/[invalid/new/' f.txt"), fs)
        assert (
            "invalid regex" in str(exc.value).lower()
            or "error" in str(exc.value).lower()
        )

    def test_unterminated_substitution(self, fs):
        fs.write("/f.txt", b"test\n")
        with pytest.raises(TerminalError) as exc:
            execute_script(to_script("sed 's/old/new' f.txt"), fs)
        assert "unterminated" in str(exc.value)

    def test_unknown_option(self, fs):
        with pytest.raises(TerminalError) as exc:
            execute_script(to_script("sed --unknown 's/a/b/'"), fs)
        assert "unknown" in str(exc.value).lower()

    def test_file_not_found(self, fs):
        with pytest.raises(TerminalError) as exc:
            execute_script(to_script("sed 's/a/b/' nonexistent.txt"), fs)
        assert "No such file" in str(exc.value)

    def test_empty_regex(self, fs):
        fs.write("/f.txt", b"test\n")
        with pytest.raises(TerminalError) as exc:
            execute_script(to_script("sed 's///g' f.txt"), fs)
        assert "empty regex" in str(exc.value)

    def test_trailing_characters_after_delete(self, fs):
        fs.write("/f.txt", b"test\n")
        with pytest.raises(TerminalError) as exc:
            execute_script(to_script("sed '1d extra' f.txt"), fs)
        assert "trailing characters" in str(exc.value)

    def test_trailing_characters_after_print(self, fs):
        fs.write("/f.txt", b"test\n")
        with pytest.raises(TerminalError) as exc:
            execute_script(to_script("sed -n '1p junk' f.txt"), fs)
        assert "trailing characters" in str(exc.value)

    def test_trailing_characters_after_substitution(self, fs):
        fs.write("/f.txt", b"test\n")
        with pytest.raises(TerminalError) as exc:
            execute_script(to_script("sed 's/a/b/g extra' f.txt"), fs)
        assert "trailing characters" in str(exc.value)


# ---------------------------------------------------------------------------
# Extended regex flag (-E / -r)
# ---------------------------------------------------------------------------


class TestSedExtendedRegex:
    def test_extended_flag_accepted(self, fs):
        """sed -E should work without error."""
        fs.write("/f.txt", b"hello world\n")
        out = execute_script(to_script("sed -E 's/hello/hi/' f.txt"), fs)
        assert out == "hi world\n"

    def test_r_flag_accepted(self, fs):
        """sed -r (alias for -E) should work without error."""
        fs.write("/f.txt", b"abc123\n")
        out = execute_script(to_script("sed -r 's/[0-9]+/NUM/' f.txt"), fs)
        assert out == "abcNUM\n"

    def test_extended_with_groups(self, fs):
        """ERE grouping with unescaped parens works (Python re default)."""
        fs.write("/f.txt", b"foo bar\n")
        out = execute_script(to_script(r"sed -E 's/(foo) (bar)/\2 \1/' f.txt"), fs)
        assert out == "bar foo\n"

    def test_extended_with_alternation(self, fs):
        """ERE alternation with | works."""
        fs.write("/f.txt", b"cat\ndog\nbird\n")
        out = execute_script(to_script("sed -E 's/cat|dog/pet/' f.txt"), fs)
        assert "pet\npet\nbird\n" == out
