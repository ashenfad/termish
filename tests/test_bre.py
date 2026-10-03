r"""grep and sed read POSIX basic regexes without ``-E``, as GNU's do.

Agents write GNU syntax, and without ``-E`` that is BRE, whose escaping
is roughly the reverse of Python's: ``\+``, ``\?``, ``\{n\}``, ``\(``
and ``\|`` are operators and their bare forms are literal. Handed to
``re`` unchanged, the operators read as literals, so a substitution
matched nothing and still exited 0 (issue #24).
"""

import pytest

from termish import MemoryFS, execute
from termish.errors import TerminalError
from termish.interpreter.commands._regex import bre_to_python

TEXT = b"aa bb\nfoo  bar\nbeta (x)\n"


@pytest.fixture
def fs():
    fs = MemoryFS()
    fs.write("/f.txt", TEXT)
    return fs


@pytest.mark.parametrize(
    "command, expected",
    [
        (r"sed 's/a\+/X/' f.txt", "X bb\nfoo  bXr\nbetX (x)\n"),
        (r"sed 's/o\{2\}/X/' f.txt", "aa bb\nfX  bar\nbeta (x)\n"),
        (r"sed 's/fo\?o/X/' f.txt", "aa bb\nX  bar\nbeta (x)\n"),
        (r"sed 's/\(foo\) *bar/\1-bar/' f.txt", "aa bb\nfoo-bar\nbeta (x)\n"),
        ("sed 's/beta (x/B/' f.txt", "aa bb\nfoo  bar\nB)\n"),
        (r"sed '/fo\+/d' f.txt", "aa bb\nbeta (x)\n"),
        (r"grep 'o\+' f.txt", "foo  bar\n"),
        (r"grep 'a\{2\}' f.txt", "aa bb\n"),
        ("grep 'beta (x' f.txt", "beta (x)\n"),
        (r"grep 'bb\|bar' f.txt", "aa bb\nfoo  bar\n"),
        (r"grep '\<bar\>' f.txt", "foo  bar\n"),
    ],
)
def test_bre_reads_as_gnu_does(fs, command, expected):
    assert execute(command, fs) == expected


@pytest.mark.parametrize(
    "command, expected",
    [
        ("sed -E 's/a+/X/' f.txt", "X bb\nfoo  bXr\nbetX (x)\n"),
        (r"sed -E 's/(foo) *bar/\1-bar/' f.txt", "aa bb\nfoo-bar\nbeta (x)\n"),
        ("grep -E 'o+' f.txt", "foo  bar\n"),
        ("grep -E 'bb|bar' f.txt", "aa bb\nfoo  bar\n"),
        (r"grep -E 'beta \(x' f.txt", "beta (x)\n"),
        ("grep -F 'beta (x' f.txt", "beta (x)\n"),
    ],
)
def test_ere_and_fixed_strings_are_unchanged(fs, command, expected):
    assert execute(command, fs) == expected


def test_a_bare_operator_is_a_literal_character(fs):
    fs.write("/g.txt", b"a+b\naab\n")
    assert execute("grep 'a+b' g.txt", fs) == "a+b\n"
    assert execute("sed 's/a+b/X/' g.txt", fs) == "X\naab\n"


@pytest.mark.parametrize(
    "bre, python",
    [
        (r"a\+", "a+"),
        (r"o\{2,3\}", "o{2,3}"),
        (r"\(a\|b\)*", "(a|b)*"),
        ("a+b?c|d(e){f}", r"a\+b\?c\|d\(e\)\{f\}"),
        ("*x", r"\*x"),
        ("^*x", r"^\*x"),
        (r"\(*x\)", r"(\*x)"),
        (r"x\|*y", r"x|\*y"),
        ("a^b$c", r"a\^b\$c"),
        (r"^ab$", "^ab$"),
        (r"\(ab$\)", "(ab$)"),
        (r"[\.]x", r"[\\.]x"),
        ("[]a]", r"[\]a]"),
        ("[^]a]", r"[^\]a]"),
        (r"\(x\)\1", r"(x)\1"),
        (r"a\.b\*", r"a\.b\*"),
        ("trail\\", "trail\\\\"),
    ],
)
def test_translation(bre, python):
    assert bre_to_python(bre) == python


# -- grep's exit status ---------------------------------------------------


def test_grep_exits_one_when_nothing_matches(fs):
    assert execute("grep zzz f.txt; echo $?", fs) == "1\n"
    assert execute("grep foo f.txt > /dev/null; echo $?", fs) == "0\n"
    assert execute("grep zzz f.txt || echo none", fs) == "none\n"


def test_grep_count_prints_zero_and_exits_one(fs):
    assert execute("grep -c zzz f.txt; echo $?", fs) == "0\n1\n"


def test_grep_quiet_follows_the_same_rule(fs):
    assert execute("grep -q foo f.txt && echo yes", fs) == "yes\n"
    assert execute("grep -q zzz f.txt || echo no", fs) == "no\n"


def test_grep_errors_exit_two(fs):
    with pytest.raises(TerminalError) as exc:
        execute("grep '[' f.txt", fs)
    assert exc.value.exit_code == 2
    with pytest.raises(TerminalError) as exc:
        execute("grep foo /missing.txt", fs)
    assert exc.value.exit_code == 2


# -- a silent failure before the last stage does not stop a pipeline ------


def test_counting_no_matches_through_a_pipe_prints_zero(fs):
    """Bash without pipefail: a pipeline's status is its last stage's,
    so ``grep x f | wc -l`` counts zero lines rather than failing."""
    assert execute("grep zzz f.txt | wc -l; echo st=$?", fs).split() == [
        "0",
        "st=0",
    ]
    assert execute("false | wc -l", fs).strip() == "0"


def test_a_failure_with_a_diagnostic_still_stops_a_pipeline(fs):
    with pytest.raises(TerminalError) as exc:
        execute("cat /missing | wc -l", fs)
    assert "No such file" in str(exc.value)
