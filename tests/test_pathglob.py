"""Pathname expansion and ``ls`` operands, as bash and GNU do them (#28).

``ls app/assets/*`` in a session listed every file in the tree twice:
``*`` crossed ``/`` and expanded to absolute paths, and ``ls`` gave each
file operand a ``path:`` header of its own. Every expected value here
was checked against bash and GNU coreutils.
"""

import pytest

from termish import MemoryFS, execute
from termish.errors import TerminalError
from termish.interpreter.pathglob import has_magic


@pytest.fixture
def fs():
    fs = MemoryFS()
    execute(
        "mkdir -p a/bg a/cards a/.git; touch a/bg/x.png a/bg/y.png a/cards/z.png "
        "a/top.txt a/.hid",
        fs,
    )
    return fs


@pytest.mark.parametrize(
    "command, expected",
    [
        # * stays within a segment, directories included
        ("echo a/*", "a/bg a/cards a/top.txt"),
        ("echo /a/*", "/a/bg /a/cards /a/top.txt"),
        ("echo a/*/*.png", "a/bg/x.png a/bg/y.png a/cards/z.png"),
        # a trailing / matches directories only, and is kept
        ("echo a/*/", "a/bg/ a/cards/"),
        # ** crosses directories, zero or more of them
        ("echo a/**/*.png", "a/bg/x.png a/bg/y.png a/cards/z.png"),
        ("echo a/**/top.txt", "a/top.txt"),
        # a final ** names everything below, files included
        ("echo a/**", "a/ a/bg a/bg/x.png a/bg/y.png a/cards a/cards/z.png a/top.txt"),
        ("echo a/**/", "a/ a/bg/ a/cards/"),
        # dotfiles only for a pattern that starts with a dot
        ("echo a/.*", "a/.git a/.hid"),
        ("echo a/.h*", "a/.hid"),
        # ? and classes, negated classes too, all one word
        ("echo a/?g", "a/bg"),
        ("echo a/[bc]*", "a/bg a/cards"),
        ("echo a/[!b]*", "a/cards a/top.txt"),
        # relative to the working directory, and kept relative
        ("cd a; echo *", "bg cards top.txt"),
        ("cd a; echo bg/*", "bg/x.png bg/y.png"),
        ("cd a; echo ../a/top*", "../a/top.txt"),
        # no match: the word stays as typed
        ("echo nope/*", "nope/*"),
        ("echo a/*.zip", "a/*.zip"),
        # quoted: never expanded
        ("echo 'a/*'", "a/*"),
        ("echo x[0]", "x[0]"),
    ],
)
def test_expansion_matches_bash(fs, command, expected):
    assert execute(command, fs).strip() == expected


def test_a_bracket_is_part_of_the_word_but_test_still_parses(fs):
    assert execute("[ -d a ] && echo dir", fs).strip() == "dir"
    assert execute("[ -f a/top.txt ]; echo $?", fs).strip() == "0"


def test_what_counts_as_a_pattern():
    assert has_magic("*.py") and has_magic("a?") and has_magic("[ab]")
    assert not has_magic("plain") and not has_magic("[") and not has_magic("x[]")


@pytest.mark.parametrize(
    "command, expected",
    [
        # files first, by name, no headers; then each directory under one
        (
            "ls a/*",
            "a/top.txt\n\na/bg:\nx.png\ny.png\n\na/cards:\nz.png\n",
        ),
        ("ls a/top.txt a/bg/x.png", "a/bg/x.png\na/top.txt\n"),
        ("ls a/bg a/cards", "a/bg:\nx.png\ny.png\n\na/cards:\nz.png\n"),
        # one directory operand: its contents, no header
        ("ls a/bg", "x.png\ny.png\n"),
        # -d lists the directories themselves
        ("ls -d a/*", "a/bg\na/cards\na/top.txt\n"),
    ],
)
def test_ls_operands_match_gnu(fs, command, expected):
    assert execute(command, fs) == expected


def test_ls_reports_a_missing_operand_and_lists_the_rest(fs):
    out = execute("ls a/top.txt nope; echo st=$?", fs)
    assert "a/top.txt\n" in out
    assert "ls: cannot access 'nope': No such file or directory" in out
    assert out.endswith("st=2\n")


def test_ls_with_nothing_to_list_is_an_error(fs):
    with pytest.raises(TerminalError, match="cannot access 'nope'"):
        execute("ls nope", fs)


PATTERNS = [
    "a/*",
    "a/*/",
    "a/*/*.png",
    "a/**",
    "a/**/",
    "a/**/*.png",
    "a/**/top.txt",
    "a/.*",
    "a/?g",
    "a/[bc]*",
    "a/[!b]*",
    "*",
    "**",
    "a/nope*",
]


@pytest.mark.parametrize("pattern", PATTERNS)
def test_expansion_agrees_with_pythons_glob(tmp_path, monkeypatch, pattern):
    """Python's glob(recursive=True) follows bash's globstar, dotfiles
    and trailing-slash rules, so a real directory tree is the reference."""
    import glob as pyglob

    for d in ("a/bg", "a/cards", "a/.git"):
        (tmp_path / d).mkdir(parents=True)
    for f in ("a/bg/x.png", "a/bg/y.png", "a/cards/z.png", "a/top.txt", "a/.hid"):
        (tmp_path / f).write_text("")
    monkeypatch.chdir(tmp_path)
    expected = sorted(pyglob.glob(pattern, recursive=True)) or [pattern]

    fs = MemoryFS()
    execute(
        "mkdir -p a/bg a/cards a/.git; touch a/bg/x.png a/bg/y.png a/cards/z.png "
        "a/top.txt a/.hid",
        fs,
    )
    assert execute(f"printf '%s\\n' {pattern}", fs).split() == expected


def test_ls_orders_operands_by_size_and_time_under_s_and_t(fs):
    fs.write("/small.txt", b"x")
    fs.write("/big.txt", b"x" * 100)
    assert execute("ls -S small.txt big.txt", fs) == "big.txt\nsmall.txt\n"
    assert execute("ls -Sr small.txt big.txt", fs) == "small.txt\nbig.txt\n"
    assert execute("ls small.txt big.txt", fs) == "big.txt\nsmall.txt\n"  # by name
