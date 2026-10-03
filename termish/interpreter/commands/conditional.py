"""``test`` and ``[``: the conditionals agents write before ``&&`` and ``||``.

``[ -f x ] && ...``, ``test -d out || mkdir out`` and ``[ -z "$VAR" ]``
are how a script asks a question, and without them each is
``command not found``. The subset is the POSIX one agents use:

- file tests: ``-e`` ``-f`` ``-d`` ``-s`` ``-r`` ``-w`` ``-x``
- string tests: ``-z`` ``-n``, ``=`` (and bash's ``==``) ``!=``, and a bare
  string, true when it is not empty
- integer tests: ``-eq`` ``-ne`` ``-lt`` ``-le`` ``-gt`` ``-ge``
- ``!``, ``-a``, ``-o`` and parentheses

True exits 0, false exits 1 with nothing written, and a malformed
expression or a non-integer operand exits 2 with a diagnostic, as in
bash. Nothing is ever written to stdout.

The filesystem has no permission bits, so ``-r`` and ``-w`` are true
for any path that exists (a read-only mount still answers true: the
protocol cannot say otherwise without writing), and ``-x`` is true for
a directory, which can be entered, and false for a file, which nothing
here can run. Out of scope: ``[[ ]]``, ``-nt``/``-ot``, ``-L``, pattern
matching.
"""

from __future__ import annotations

import re

from termish.context import CommandContext, CommandResult
from termish.fs import FileSystem

from ._util import resolve_path

_UNARY = {"-e", "-f", "-d", "-s", "-r", "-w", "-x", "-z", "-n"}
_BINARY = {"=", "==", "!=", "-eq", "-ne", "-lt", "-le", "-gt", "-ge"}
_INTEGER = re.compile(r"^\s*[+-]?\d+\s*$")
#: bash's integers are signed 64-bit; past either end is not an integer.
_INT_MIN, _INT_MAX = -(2**63), 2**63 - 1


class _Usage(Exception):
    """A malformed expression or a bad operand: exit 2, with this text."""


def test_cmd(ctx: CommandContext) -> CommandResult | None:
    """``test EXPRESSION`` — exit 0 when it holds, 1 when it does not."""
    return _run("test", list(ctx.args), ctx.fs)


def bracket_cmd(ctx: CommandContext) -> CommandResult | None:
    """``[ EXPRESSION ]`` — ``test``, with a closing ``]`` required."""
    args = list(ctx.args)
    if not args or args[-1] != "]":
        return CommandResult(exit_code=2, stderr="[: missing `]'")
    return _run("[", args[:-1], ctx.fs)


def _run(name: str, args: list[str], fs: FileSystem) -> CommandResult | None:
    try:
        held = _evaluate(args, fs)
    except _Usage as e:
        return CommandResult(exit_code=2, stderr=f"{name}: {e}")
    return None if held else CommandResult(exit_code=1, stderr="")


def _evaluate(args: list[str], fs: FileSystem) -> bool:
    """POSIX reads one to four arguments by their count, which is what
    makes ``[ -n ]`` (a bare string) and ``[ ! = x ]`` mean what they do;
    longer expressions go to the general parser."""
    n = len(args)
    if n == 0:
        return False
    if n == 1:
        return args[0] != ""
    if n == 2:
        if args[0] == "!":
            return not _evaluate(args[1:], fs)
        if args[0] in _UNARY:
            return _unary(args[0], args[1], fs)
        raise _Usage(f"{args[0]}: unary operator expected")
    if n == 3:
        if args[1] in _BINARY:
            return _binary(args[0], args[1], args[2])
        if args[0] == "!":
            return not _evaluate(args[1:], fs)
        if args[0] == "(" and args[2] == ")":
            return _evaluate(args[1:2], fs)
        if args[1] in ("-a", "-o"):
            return _Parser(args, fs).parse()
        raise _Usage(f"{args[1]}: binary operator expected")
    if n == 4:
        if args[0] == "!":
            return not _evaluate(args[1:], fs)
        if args[0] == "(" and args[3] == ")":
            return _evaluate(args[1:3], fs)
    return _Parser(args, fs).parse()


class _Parser:
    """``-o`` binds loosest, then ``-a``, then ``!``; a primary is a
    parenthesised expression, a unary test, a binary test or a bare
    string."""

    def __init__(self, args: list[str], fs: FileSystem) -> None:
        self.args = args
        self.fs = fs
        self.i = 0

    def parse(self) -> bool:
        value = self._or()
        if self.i < len(self.args):
            raise _Usage(f"{self.args[self.i]}: unexpected argument")
        return value

    def _peek(self, ahead: int = 0) -> str | None:
        j = self.i + ahead
        return self.args[j] if j < len(self.args) else None

    def _or(self) -> bool:
        value = self._and()
        while self._peek() == "-o":
            self.i += 1
            right = self._and()
            value = value or right
        return value

    def _and(self) -> bool:
        value = self._not()
        while self._peek() == "-a":
            self.i += 1
            right = self._not()
            value = value and right
        return value

    def _not(self) -> bool:
        if self._peek() == "!":
            self.i += 1
            return not self._not()
        return self._primary()

    def _primary(self) -> bool:
        tok = self._peek()
        if tok is None:
            raise _Usage("argument expected")
        if tok == "(":
            self.i += 1
            value = self._or()
            if self._peek() != ")":
                raise _Usage("')' expected")
            self.i += 1
            return value
        if tok in _UNARY and self._peek(1) is not None:
            self.i += 2
            return _unary(tok, self.args[self.i - 1], self.fs)
        if self._peek(1) in _BINARY and self._peek(2) is not None:
            left, op, right = self.args[self.i : self.i + 3]
            self.i += 3
            return _binary(left, op, right)
        self.i += 1
        return tok != ""


def _unary(op: str, operand: str, fs: FileSystem) -> bool:
    if op == "-z":
        return operand == ""
    if op == "-n":
        return operand != ""
    if operand == "":
        # No path at all, not the current directory: an unset variable
        # in `[ -d "$OUT" ]` must not test whatever the shell stands in.
        return False
    path = resolve_path(operand, fs)
    if not fs.exists(path):
        return False
    if op in ("-e", "-r", "-w"):
        return True
    if op == "-f":
        return fs.isfile(path)
    if op == "-d":
        return fs.isdir(path)
    if op == "-x":
        return fs.isdir(path)
    # -s: exists and is not empty; a directory counts as not empty, as a
    # real directory's own size does
    if fs.isdir(path):
        return True
    return fs.stat(path).size > 0


def _binary(left: str, op: str, right: str) -> bool:
    if op in ("=", "=="):
        return left == right
    if op == "!=":
        return left != right
    a, b = _integer(left), _integer(right)
    return {
        "-eq": a == b,
        "-ne": a != b,
        "-lt": a < b,
        "-le": a <= b,
        "-gt": a > b,
        "-ge": a >= b,
    }[op]


def _integer(text: str) -> int:
    value = int(text.strip()) if _INTEGER.match(text) else None
    if value is None or not _INT_MIN <= value <= _INT_MAX:
        raise _Usage(f"{text}: integer expression expected")
    return value
