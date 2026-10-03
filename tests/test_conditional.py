"""``test`` and ``[`` (issue #23).

Every expected value here was checked against real bash: the issue's
conformance bar is that these behave identically on the termish and
real-bash rungs.
"""

import pytest

from termish import MemoryFS, execute


@pytest.fixture
def fs():
    fs = MemoryFS()
    execute("mkdir out; echo hi > a.txt; touch empty.txt", fs)
    return fs


@pytest.mark.parametrize(
    "command, expected",
    [
        # the issue's conformance bar
        ("[ -f a.txt ] && echo yes", "yes\n"),
        ("[ ! -d new ] && mkdir new && echo made", "made\n"),
        ('test "$X" = "" && echo empty', "empty\n"),
        # file tests
        ("test -d out || echo no", ""),
        ("[ -e nope ]; echo $?", "1\n"),
        ("[ -s empty.txt ] || echo empty", "empty\n"),
        ("[ -s a.txt ] && echo full", "full\n"),
        ("[ -x out ] && echo enterable", "enterable\n"),
        ("[ -x a.txt ] || echo not-runnable", "not-runnable\n"),
        ("[ -r a.txt -a -w a.txt ] && echo rw", "rw\n"),
        # an empty operand names no path: every file test on it is false
        ('[ -e "$UNSET" ]; echo $?', "1\n"),
        ('[ -d "$UNSET" ] || echo no-dir', "no-dir\n"),
        ('[ -f "" ]; echo $?', "1\n"),
        ('[ -s "" ]; echo $?', "1\n"),
        ('[ -r "" -o -w "" -o -x "" ]; echo $?', "1\n"),
        # strings
        ('[ -z "$X" ] && echo z', "z\n"),
        ('[ -n "$X" ] || echo unset', "unset\n"),
        ("[ a == a ]; echo $?", "0\n"),
        ("[ a != a ]; echo $?", "1\n"),
        ('[ "" ]; echo $?', "1\n"),
        ("[ -n ] && echo bare", "bare\n"),  # one argument: a non-empty string
        # integers
        ("[ 3 -lt 10 ] && echo lt", "lt\n"),
        ("[ 05 -eq 5 ]; echo $?", "0\n"),
        ("[ -3 -lt 2 ]; echo $?", "0\n"),
        ("[ 9223372036854775807 -gt 0 ]; echo $?", "0\n"),
        ("[ -9223372036854775808 -lt 0 ]; echo $?", "0\n"),
        # logic
        ("[ ! ]; echo $?", "0\n"),
        ("[ ! = x ]; echo $?", "1\n"),  # three arguments: '=' is the operator
        ("[ ! ! -f a.txt ]; echo $?", "0\n"),
        ("[ a = a -a b != c ]; echo $?", "0\n"),
        ("[ -d out -o -f nope ]; echo $?", "0\n"),
        (r"[ \( -f nope -o -f a.txt \) -a ! -d a.txt ] && echo paren", "paren\n"),
        ("test; echo $?", "1\n"),
        ("[ -f a.txt ] && [ -d out ] && echo chain", "chain\n"),
    ],
)
def test_matches_bash(fs, command, expected):
    assert execute(command, fs) == expected


@pytest.mark.parametrize(
    "command, message",
    [
        ("[ abc -eq 1 ]; echo $?", "[: abc: integer expression expected\n2\n"),
        # signed 64-bit, as in bash: one past either end is not an integer
        (
            "[ 9223372036854775808 -gt 0 ]; echo $?",
            "[: 9223372036854775808: integer expression expected\n2\n",
        ),
        (
            "[ -9223372036854775809 -lt 0 ]; echo $?",
            "[: -9223372036854775809: integer expression expected\n2\n",
        ),
        ("[ -f a.txt ; echo $?", "[: missing `]'\n2\n"),
        ("test 1 -gt; echo $?", "test: 1: unary operator expected\n2\n"),
    ],
)
def test_a_usage_error_exits_two_with_a_diagnostic(fs, command, message):
    assert execute(command, fs) == message


def test_nothing_reaches_stdout(fs):
    assert execute("[ -f a.txt ] | wc -c", fs).strip() == "0"
    assert execute("test -d out > /out.txt; cat /out.txt", fs) == ""
