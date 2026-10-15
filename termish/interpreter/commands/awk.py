"""``awk``: a bounded awk, read by a small lexer and parser and run by a
tree-walking interpreter. termish-ts's ``awk.ts`` is its twin, and the
two run the same cases (``tests/cases/awk.json``).

What it runs: ``awk [-F fs] [-v var=value]... ('program' | -f file)
[file | var=value]...``, reading stdin when no file is named. Patterns
(BEGIN, END, expressions, ``/re/``, ranges ``p1, p2``); statements
(``print``, ``printf``, ``if``/``else``, ``while``, ``do``, ``for``,
``for (k in a)``, ``break``, ``continue``, ``next``, ``exit``,
``delete``); ``print > file`` and ``>>``; fields, assignment to fields
and ``NF``; associative arrays; the usual variables (NR NF FNR FS OFS ORS
RS FILENAME SUBSEP RSTART RLENGTH CONVFMT OFMT ENVIRON ARGV ARGC); and the
builtins length, substr, index, split, sub, gsub, match, sprintf,
tolower, toupper, int, sqrt, exp, log, sin, cos, atan2, close and fflush.

Refused, with an error naming it: ``getline``, ``system()``, ``rand()``
and ``srand()``, user-defined functions, and pipes in ``print``.

Values follow awk: a number, a string, or a string from input (a field, a
``-v`` value, a ``split`` element) that compares as a number when it
looks like one; an unset variable is both "" and 0. Numbers print as
integers when integral and through ``%.6g`` otherwise, rounded as C
rounds (exactly, half to even).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Iterator, Union

from termish.context import CommandContext, CommandResult

# ---------------------------------------------------------------------------
# Errors and control flow
# ---------------------------------------------------------------------------


class AwkSyntaxError(Exception):
    """A program that can't run: a syntax error, or something refused."""


class AwkRuntimeError(Exception):
    """A fatal error while running (division by zero, an unreadable file)."""


class _Next(Exception):
    pass


class _Exit(Exception):
    def __init__(self, code: int | None) -> None:
        # None: ``exit`` with no status, which keeps the one set before.
        self.code = code


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Token:
    kind: str  # num str ere name funcname builtin kw op nl eof
    value: str
    num: float
    pos: int


KEYWORDS = {
    "BEGIN",
    "END",
    "function",
    "func",
    "if",
    "else",
    "while",
    "for",
    "do",
    "break",
    "continue",
    "next",
    "nextfile",
    "exit",
    "return",
    "delete",
    "in",
    "getline",
    "print",
    "printf",
}

BUILTINS = {
    "length",
    "substr",
    "index",
    "split",
    "sub",
    "gsub",
    "match",
    "sprintf",
    "tolower",
    "toupper",
    "int",
    "sqrt",
    "exp",
    "log",
    "sin",
    "cos",
    "atan2",
    "close",
    "fflush",
    "system",
    "rand",
    "srand",
}

# Builtins refused by name.
REFUSED_CALLS = {"system", "rand", "srand"}

# Operators, longest first so ``**=`` wins over ``**`` and ``*``.
OPERATORS = [
    "**=",
    "&&",
    "||",
    "==",
    "<=",
    ">=",
    "!=",
    "++",
    "--",
    "+=",
    "-=",
    "*=",
    "/=",
    "%=",
    "^=",
    ">>",
    "!~",
    "**",
    "{",
    "}",
    "(",
    ")",
    "[",
    "]",
    ";",
    ",",
    "+",
    "-",
    "*",
    "/",
    "%",
    "^",
    "!",
    ">",
    "<",
    "|",
    "?",
    ":",
    "~",
    "$",
    "=",
]


def _divides(prev: Token | None) -> bool:
    """Tokens after which a ``/`` divides; after anything else it starts a regex."""
    if prev is None:
        return False
    if prev.kind in ("num", "str", "ere", "name", "builtin"):
        return True
    return prev.kind == "op" and prev.value in (")", "]", "$", "++", "--")


_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "\\": "\\",
    '"': '"',
    "/": "/",
    "a": "\x07",
    "b": "\b",
    "f": "\f",
    "v": "\v",
}


def read_escapes(text: str) -> str:
    """Read awk's escapes in ``text`` (a string literal's inside, a ``-v``
    value, ``-F``): ``\\n \\t \\r \\\\ \\" \\/ \\a \\b \\f \\v`` and octal
    ``\\ooo``. Any other escape keeps its backslash, so ``"\\."`` reaches a
    regex as ``\\.``."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch != "\\" or i + 1 >= n:
            out.append(ch)
            i += 1
            continue
        nxt = text[i + 1]
        if nxt in _ESCAPES:
            out.append(_ESCAPES[nxt])
            i += 2
        elif "0" <= nxt <= "7":
            j = i + 1
            code = 0
            while j < n and j < i + 4 and "0" <= text[j] <= "7":
                code = code * 8 + int(text[j])
                j += 1
            out.append(chr(code))
            i = j
        else:
            out.append("\\" + nxt)
            i += 2
    return "".join(out)


_NUMBER_RE = re.compile(
    r"(?:[0-9]+\.?[0-9]*(?:[eE][+-]?[0-9]+)?|\.[0-9]+(?:[eE][+-]?[0-9]+)?)"
)
_ASCII_DIGIT = re.compile(r"[0-9]")
_NAME_START = re.compile(r"[A-Za-z_]")
_NAME_CHAR = re.compile(r"[A-Za-z0-9_]")


def _tokenize(src: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    n = len(src)

    def push(kind: str, value: str, pos: int, num: float = 0.0) -> None:
        tokens.append(Token(kind, value, num, pos))

    def last() -> Token | None:
        return tokens[-1] if tokens else None

    while i < n:
        ch = src[i]
        if ch in " \t\r":
            i += 1
            continue
        if ch == "\\" and src[i + 1 : i + 2] == "\n":
            i += 2
            continue
        if ch == "\\" and src[i + 1 : i + 3] == "\r\n":
            i += 3
            continue
        if ch == "#":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if ch == "\n":
            prev = last()
            if prev is None or prev.kind != "nl":
                push("nl", "\n", i)
            i += 1
            continue
        if ch == '"':
            start = i
            i += 1
            raw: list[str] = []
            while i < n and src[i] != '"':
                if src[i] == "\n":
                    raise _syntax_at(src, start, "unterminated string")
                if src[i] == "\\" and i + 1 < n:
                    raw.append(src[i : i + 2])
                    i += 2
                    continue
                raw.append(src[i])
                i += 1
            if i >= n:
                raise _syntax_at(src, start, "unterminated string")
            i += 1
            push("str", read_escapes("".join(raw)), start)
            continue
        if ch == "/" and not _divides(last()):
            start = i
            i += 1
            raw = []
            in_bracket = False
            while i < n:
                c = src[i]
                if c == "\n":
                    break
                if c == "\\" and i + 1 < n:
                    # ``\/`` is a slash; every other escape is the regex's own
                    raw.append("/" if src[i + 1] == "/" else src[i : i + 2])
                    i += 2
                    continue
                if in_bracket:
                    if c == "]":
                        in_bracket = False
                elif c == "[":
                    in_bracket = True
                    raw.append(c)
                    i += 1
                    # a ``]`` (after an optional ``^``) first in a bracket is a member
                    if src[i : i + 1] == "^":
                        raw.append("^")
                        i += 1
                    if src[i : i + 1] == "]":
                        raw.append("]")
                        i += 1
                    continue
                elif c == "/":
                    break
                raw.append(c)
                i += 1
            if src[i : i + 1] != "/":
                raise _syntax_at(src, start, "unterminated regex")
            i += 1
            push("ere", "".join(raw), start)
            continue
        if _ASCII_DIGIT.match(ch) or (
            ch == "." and _ASCII_DIGIT.match(src[i + 1 : i + 2])
        ):
            m = _NUMBER_RE.match(src, i)
            assert m is not None
            push("num", m.group(0), i, float(m.group(0)))
            i = m.end()
            continue
        if _NAME_START.match(ch):
            start = i
            while i < n and _NAME_CHAR.match(src[i]):
                i += 1
            word = src[start:i]
            if word in KEYWORDS:
                push("kw", word, start)
            elif word in BUILTINS:
                push("builtin", word, start)
            elif src[i : i + 1] == "(":
                push("funcname", word, start)
            else:
                push("name", word, start)
            continue
        op = next((o for o in OPERATORS if src.startswith(o, i)), None)
        if op is None:
            raise _syntax_at(src, i, f"unexpected character '{ch}'")
        push("op", op, i)
        i += len(op)
    push("eof", "", n)
    return tokens


def _syntax_at(src: str, pos: int, what: str) -> AwkSyntaxError:
    before = src[:pos]
    line = before.count("\n") + 1
    column = pos - before.rfind("\n")
    return AwkSyntaxError(f"awk: syntax error at line {line}, column {column}: {what}")


def _refused(what: str) -> AwkSyntaxError:
    return AwkSyntaxError(f"awk: {what}")


# ---------------------------------------------------------------------------
# Syntax tree: expressions and statements are tuples, tagged first
# ---------------------------------------------------------------------------

# Expressions:
#   ("num", v) ("str", v) ("ere", src) ("var", name) ("idx", name, subs)
#   ("field", e) ("assign", op, target, e) ("cond", c, a, b)
#   ("and", a, b) ("or", a, b) ("in", subs, name) ("match", neg, a, re)
#   ("cmp", op, a, b) ("concat", a, b) ("bin", op, a, b)
#   ("neg", e) ("pos", e) ("not", e) ("incr", op, prefix, target)
#   ("call", name, args) ("group", e)
# Statements:
#   ("block", body) ("expr", e) ("print"|"printf", args, dest)
#   ("if", c, then, else) ("while", c, body) ("do", body, c)
#   ("for", init, c, step, body) ("forin", name, arr, body)
#   ("break",) ("continue",) ("next",) ("exit", e) ("delete", name, subs)

Expr = tuple
Stmt = tuple


@dataclass
class Item:
    kind: str  # begin end main
    pattern: Expr | None
    until: Expr | None
    action: Stmt | None
    in_range: bool = False


ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "^=", "**="}
COMPARE_OPS = {"<", "<=", "==", "!=", ">=", ">"}

_ARITY: dict[str, tuple[int, float]] = {
    "length": (0, 1),
    "substr": (2, 3),
    "index": (2, 2),
    "split": (2, 3),
    "sub": (2, 3),
    "gsub": (2, 3),
    "match": (2, 2),
    "sprintf": (1, math.inf),
    "tolower": (1, 1),
    "toupper": (1, 1),
    "int": (1, 1),
    "sqrt": (1, 1),
    "exp": (1, 1),
    "log": (1, 1),
    "sin": (1, 1),
    "cos": (1, 1),
    "atan2": (2, 2),
    "close": (1, 1),
    "fflush": (0, 1),
}


def _is_lvalue(e: Expr) -> bool:
    return e[0] in ("var", "idx", "field")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class Parser:
    def __init__(self, src: str) -> None:
        self._src = src
        self._tokens = _tokenize(src)
        self._i = 0
        # Within ``print``'s arguments, outside parentheses: ``>`` redirects.
        self._print_args = False

    def parse(self) -> list[Item]:
        items: list[Item] = []
        self._skip_terminators()
        while not self._at("eof"):
            items.append(self._item())
            self._skip_terminators()
        return items

    # -- token helpers ------------------------------------------------------

    def _peek(self, offset: int = 0) -> Token:
        return self._tokens[min(self._i + offset, len(self._tokens) - 1)]

    def _next(self) -> Token:
        tok = self._peek()
        if self._i < len(self._tokens) - 1:
            self._i += 1
        return tok

    def _at(self, kind: str, value: str | None = None) -> bool:
        tok = self._peek()
        return tok.kind == kind and (value is None or tok.value == value)

    def _at_op(self, value: str) -> bool:
        return self._at("op", value)

    def _at_kw(self, value: str) -> bool:
        return self._at("kw", value)

    def _expect_op(self, value: str) -> Token:
        if not self._at_op(value):
            raise self._unexpected(f"expected '{value}'")
        return self._next()

    def _skip_newlines(self) -> None:
        while self._at("nl"):
            self._next()

    def _skip_terminators(self) -> None:
        while self._at("nl") or self._at_op(";"):
            self._next()

    def _error(self, tok: Token, what: str) -> AwkSyntaxError:
        return _syntax_at(self._src, tok.pos, what)

    def _unexpected(self, expected: str | None = None) -> AwkSyntaxError:
        tok = self._peek()
        if tok.kind == "kw" and tok.value == "getline":
            return _refused("getline is not supported")
        if tok.kind == "op" and tok.value == "|":
            after = self._peek(1)
            if after.kind == "kw" and after.value == "getline":
                return _refused("getline is not supported")
            return _refused("pipes are not supported")
        if tok.kind == "eof":
            shown = "end of program"
        elif tok.kind == "nl":
            shown = "newline"
        else:
            shown = f"'{tok.value}'"
        suffix = f" ({expected})" if expected else ""
        return self._error(tok, f"unexpected {shown}{suffix}")

    # -- program ------------------------------------------------------------

    def _item(self) -> Item:
        tok = self._peek()
        if tok.kind == "kw" and tok.value in ("BEGIN", "END"):
            self._next()
            self._skip_newlines()
            if not self._at_op("{"):
                raise self._error(tok, f"{tok.value} needs an action")
            action = self._block()
            return Item("begin" if tok.value == "BEGIN" else "end", None, None, action)
        if tok.kind == "kw" and tok.value in ("function", "func"):
            raise _refused("user-defined functions are not supported")
        if self._at_op("{"):
            return Item("main", None, None, self._block())
        pattern = self._expr()
        until = None
        if self._at_op(","):
            self._next()
            self._skip_newlines()
            until = self._expr()
        if self._at_op("{"):
            return Item("main", pattern, until, self._block())
        if not (self._at("nl") or self._at_op(";") or self._at("eof")):
            raise self._unexpected()
        return Item("main", pattern, until, None)

    def _block(self) -> Stmt:
        self._expect_op("{")
        body: list[Stmt] = []
        while True:
            self._skip_terminators()
            if self._at_op("}"):
                break
            if self._at("eof"):
                raise self._unexpected("expected '}'")
            body.append(self._statement())
            # a statement ends at a newline, ``;`` or ``}``, or right after a block
            prev = self._tokens[self._i - 1]
            closed = prev.kind == "op" and prev.value == "}"
            if not (closed or self._at("nl") or self._at_op(";") or self._at_op("}")):
                raise self._unexpected()
        self._next()
        return ("block", body)

    def _body(self) -> Stmt:
        """The statement a loop or ``if`` runs: a lone ``;`` is an empty one."""
        self._skip_newlines()
        if self._at_op(";"):
            self._next()
            return ("block", [])
        return self._statement()

    def _statement(self) -> Stmt:
        tok = self._peek()
        if tok.kind == "op" and tok.value == "{":
            return self._block()
        if tok.kind == "op" and tok.value == ";":
            self._next()
            return ("block", [])
        if tok.kind == "kw":
            v = tok.value
            if v == "if":
                self._next()
                self._expect_op("(")
                c = self._expr()
                self._expect_op(")")
                then = self._body()
                # ``else`` may follow a newline or ``;``
                j = 0
                while self._peek(j).kind == "nl" or (
                    self._peek(j).kind == "op" and self._peek(j).value == ";"
                ):
                    j += 1
                if self._peek(j).kind == "kw" and self._peek(j).value == "else":
                    self._i += j + 1
                    return ("if", c, then, self._body())
                return ("if", c, then, None)
            if v == "while":
                self._next()
                self._expect_op("(")
                c = self._expr()
                self._expect_op(")")
                if self._at_op(";"):
                    self._next()
                    return ("while", c, ("block", []))
                return ("while", c, self._body())
            if v == "do":
                self._next()
                body = self._body()
                self._skip_terminators()
                if not self._at_kw("while"):
                    raise self._unexpected("expected 'while'")
                self._next()
                self._expect_op("(")
                c = self._expr()
                self._expect_op(")")
                return ("do", body, c)
            if v == "for":
                return self._for()
            if v in ("break", "continue", "next"):
                self._next()
                return (v,)
            if v == "nextfile":
                raise _refused("nextfile is not supported")
            if v == "return":
                raise _refused(
                    "return is not supported: there are no user-defined functions"
                )
            if v == "getline":
                raise _refused("getline is not supported")
            if v == "exit":
                self._next()
                ends = (
                    self._at("nl")
                    or self._at_op(";")
                    or self._at_op("}")
                    or self._at("eof")
                )
                return ("exit", None if ends else self._expr())
            if v == "delete":
                self._next()
                name = self._peek()
                if name.kind != "name":
                    raise self._unexpected("expected an array name")
                self._next()
                if not self._at_op("["):
                    return ("delete", name.value, None)
                return ("delete", name.value, self._subscripts())
            if v in ("print", "printf"):
                return self._print(v)
        return ("expr", self._expr())

    def _for(self) -> Stmt:
        self._next()
        self._expect_op("(")
        a, b, c, d = self._peek(), self._peek(1), self._peek(2), self._peek(3)
        if (
            a.kind == "name"
            and b.kind == "kw"
            and b.value == "in"
            and c.kind == "name"
            and d.kind == "op"
            and d.value == ")"
        ):
            self._i += 4
            return ("forin", a.value, c.value, self._body())
        init = None if self._at_op(";") else self._expr()
        self._expect_op(";")
        self._skip_newlines()
        cond = None if self._at_op(";") else self._expr()
        self._expect_op(";")
        self._skip_newlines()
        step = None if self._at_op(")") else self._expr()
        self._expect_op(")")
        if self._at_op(";"):
            self._next()
            return ("for", init, cond, step, ("block", []))
        return ("for", init, cond, step, self._body())

    def _print(self, kind: str) -> Stmt:
        self._next()
        args: list[Expr] = []

        def ends() -> bool:
            return (
                self._at("nl")
                or self._at("eof")
                or self._at_op(";")
                or self._at_op("}")
                or self._at_op(">")
                or self._at_op(">>")
                or self._at_op("|")
            )

        if not ends():
            # ``print (a, b) > "f"``: a parenthesized list is the arguments
            grouped = False
            if self._at_op("("):
                save = self._i
                self._next()
                lst = self._expr_list()
                if self._at_op(")"):
                    self._next()
                    if ends():
                        args = lst
                        grouped = True
                if not grouped:
                    self._i = save
            if not grouped:
                outer = self._print_args
                self._print_args = True
                try:
                    args = self._expr_list()
                finally:
                    self._print_args = outer
        if kind == "printf" and not args:
            raise self._unexpected("printf needs a format")
        dest = None
        if self._at_op(">") or self._at_op(">>"):
            mode = self._next().value
            dest = (mode, self._concat())
        elif self._at_op("|"):
            raise _refused("pipes in print are not supported")
        return (kind, args, dest)

    def _expr_list(self) -> list[Expr]:
        lst = [self._expr()]
        while self._at_op(","):
            self._next()
            self._skip_newlines()
            lst.append(self._expr())
        return lst

    def _subscripts(self) -> list[Expr]:
        self._expect_op("[")
        outer = self._print_args
        self._print_args = False
        try:
            subs = self._expr_list()
            self._expect_op("]")
            return subs
        finally:
            self._print_args = outer

    # -- expressions, lowest precedence first ------------------------------

    def _expr(self) -> Expr:
        left = self._ternary()
        tok = self._peek()
        if tok.kind == "op" and tok.value in ASSIGN_OPS:
            if not _is_lvalue(left):
                raise self._unexpected("assignment to a non-variable")
            op = self._next().value
            self._skip_newlines()
            return ("assign", op, left, self._expr())
        return left

    def _ternary(self) -> Expr:
        c = self._or()
        if not self._at_op("?"):
            return c
        self._next()
        self._skip_newlines()
        a = self._expr()
        self._skip_newlines()
        self._expect_op(":")
        self._skip_newlines()
        return ("cond", c, a, self._expr())

    def _or(self) -> Expr:
        a = self._and()
        while self._at_op("||"):
            self._next()
            self._skip_newlines()
            a = ("or", a, self._and())
        return a

    def _and(self) -> Expr:
        a = self._in()
        while self._at_op("&&"):
            self._next()
            self._skip_newlines()
            a = ("and", a, self._in())
        return a

    def _in(self) -> Expr:
        a = self._match()
        while self._at_kw("in"):
            self._next()
            name = self._peek()
            if name.kind != "name":
                raise self._unexpected("expected an array name")
            self._next()
            a = ("in", [a], name.value)
        return a

    def _match(self) -> Expr:
        a = self._compare()
        while self._at_op("~") or self._at_op("!~"):
            neg = self._next().value == "!~"
            a = ("match", neg, a, self._compare())
        return a

    def _compare(self) -> Expr:
        a = self._concat()
        tok = self._peek()
        if tok.kind != "op" or tok.value not in COMPARE_OPS:
            return a
        if tok.value == ">" and self._print_args:
            return a
        self._next()
        return ("cmp", tok.value, a, self._concat())

    def _starts_concat(self) -> bool:
        """Whether the next token starts an operand of concatenation."""
        tok = self._peek()
        if tok.kind in ("num", "str", "ere", "name", "funcname", "builtin"):
            return True
        if tok.kind == "kw":
            return tok.value == "getline"
        return tok.kind == "op" and tok.value in ("$", "(")

    def _concat(self) -> Expr:
        a = self._additive()
        while self._starts_concat():
            a = ("concat", a, self._additive())
        return a

    def _additive(self) -> Expr:
        a = self._multiplicative()
        while self._at_op("+") or self._at_op("-"):
            op = self._next().value
            a = ("bin", op, a, self._multiplicative())
        return a

    def _multiplicative(self) -> Expr:
        a = self._unary()
        while self._at_op("*") or self._at_op("/") or self._at_op("%"):
            op = self._next().value
            a = ("bin", op, a, self._unary())
        return a

    def _unary(self) -> Expr:
        if self._at_op("!"):
            self._next()
            return ("not", self._unary())
        if self._at_op("-"):
            self._next()
            return ("neg", self._unary())
        if self._at_op("+"):
            self._next()
            return ("pos", self._unary())
        return self._power()

    def _power(self) -> Expr:
        a = self._incr()
        if self._at_op("^") or self._at_op("**"):
            self._next()
            # right-associative, and ``2^-1`` takes its sign
            return ("bin", "^", a, self._unary())
        return a

    def _incr(self) -> Expr:
        if self._at_op("++") or self._at_op("--"):
            op = self._next().value
            target = self._incr()
            if not _is_lvalue(target):
                raise self._unexpected(f"{op} needs a variable")
            return ("incr", op, True, target)
        e = self._field()
        if _is_lvalue(e) and (self._at_op("++") or self._at_op("--")):
            op = self._next().value
            return ("incr", op, False, e)
        return e

    def _field(self) -> Expr:
        if not self._at_op("$"):
            return self._primary()
        self._next()
        if self._at_op("++") or self._at_op("--"):
            op = self._next().value
            target = self._field()
            if not _is_lvalue(target):
                raise self._unexpected(f"{op} needs a variable")
            return ("field", ("incr", op, True, target))
        if self._at_op("-"):
            self._next()
            return ("field", ("neg", self._field()))
        return ("field", self._field())

    def _primary(self) -> Expr:
        tok = self._peek()
        if tok.kind == "num":
            self._next()
            return ("num", tok.num)
        if tok.kind == "str":
            self._next()
            return ("str", tok.value)
        if tok.kind == "ere":
            self._next()
            return ("ere", tok.value)
        if tok.kind == "name":
            self._next()
            if self._at_op("["):
                return ("idx", tok.value, self._subscripts())
            return ("var", tok.value)
        if tok.kind == "funcname":
            raise _refused(
                f"calling undefined function {tok.value}: "
                "user-defined functions are not supported"
            )
        if tok.kind == "builtin":
            return self._call()
        if tok.kind == "op":
            if tok.value == "(":
                self._next()
                outer = self._print_args
                self._print_args = False
                try:
                    lst = self._expr_list()
                    self._expect_op(")")
                    if len(lst) > 1:
                        if not self._at_kw("in"):
                            raise self._unexpected(
                                "expected 'in' after a parenthesized list"
                            )
                        self._next()
                        name = self._peek()
                        if name.kind != "name":
                            raise self._unexpected("expected an array name")
                        self._next()
                        return ("in", lst, name.value)
                    return ("group", lst[0])
                finally:
                    self._print_args = outer
            if tok.value in ("-", "+", "!"):
                return self._unary()
        raise self._unexpected()

    def _call(self) -> Expr:
        tok = self._next()
        name = tok.value
        if name in REFUSED_CALLS:
            raise _refused(f"{name}() is not supported")
        if not self._at_op("("):
            if name == "length":
                return ("call", name, [])
            raise self._unexpected(f"expected '(' after {name}")
        self._next()
        outer = self._print_args
        self._print_args = False
        args: list[Expr] = []
        try:
            self._skip_newlines()
            if not self._at_op(")"):
                args = self._expr_list()
            self._skip_newlines()
            self._expect_op(")")
        finally:
            self._print_args = outer
        lo, hi = _ARITY[name]
        if len(args) < lo or len(args) > hi:
            count = str(lo) if lo == hi else f"{lo} to {int(hi)}"
            raise self._error(tok, f"{name}() takes {count} arguments")
        if name == "split" and args[1][0] != "var":
            raise self._error(tok, "split()'s second argument must be an array name")
        if name in ("sub", "gsub") and len(args) == 3 and not _is_lvalue(args[2]):
            raise self._error(
                tok, f"{name}()'s third argument must be a variable or field"
            )
        return ("call", name, args)


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


class StrNum:
    """A string from input (a field, a ``-v`` value, a ``split`` element):
    it compares as a number when it looks like one."""

    __slots__ = ("s",)

    def __init__(self, s: str) -> None:
        self.s = s


class _Uninit:
    """An unset variable or element: both "" and 0."""

    __slots__ = ()


UNINIT = _Uninit()

Val = Union[float, str, StrNum, _Uninit]
AwkArray = dict

_LEADING_NUMBER = re.compile(
    r"[ \t\n\r\f\v]*[+-]?(?:[0-9]+\.?[0-9]*(?:[eE][+-]?[0-9]+)?"
    r"|\.[0-9]+(?:[eE][+-]?[0-9]+)?)"
)
_WHOLE_NUMBER = re.compile(
    r"[ \t\n\r\f\v]*[+-]?(?:[0-9]+\.?[0-9]*(?:[eE][+-]?[0-9]+)?"
    r"|\.[0-9]+(?:[eE][+-]?[0-9]+)?)[ \t\n\r\f\v]*"
)


def _str_to_num(s: str) -> float:
    """The number a string starts with, as ``strtod`` reads it; 0 for none."""
    m = _LEADING_NUMBER.match(s)
    return float(m.group(0)) if m else 0.0


def _looks_numeric(v: Val) -> bool:
    if isinstance(v, float) or v is UNINIT:
        return True
    return isinstance(v, StrNum) and _WHOLE_NUMBER.fullmatch(v.s) is not None


def _to_num(v: Val) -> float:
    if isinstance(v, float):
        return v
    if v is UNINIT:
        return 0.0
    return _str_to_num(v if isinstance(v, str) else v.s)  # type: ignore[union-attr]


def _num_to_str(n: float, fmt: str) -> str:
    """A number as text: an integral one as an integer, any other through ``fmt``."""
    if math.isfinite(n) and n == int(n):
        return str(int(n))
    return _format_printf(fmt, [n])


def _is_true(v: Val) -> bool:
    if isinstance(v, float):
        return v != 0
    if v is UNINIT:
        return False
    if isinstance(v, str):
        return v != ""
    s = v.s  # type: ignore[union-attr]
    return _str_to_num(s) != 0 if _WHOLE_NUMBER.fullmatch(s) else s != ""


def _val_to_str(v: Val, convfmt: str) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, float):
        return _num_to_str(v, convfmt)
    if v is UNINIT:
        return ""
    return v.s  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Number formatting, as C's printf formats (exact, half to even)
# ---------------------------------------------------------------------------


def _fixed_digits(x: float, p: int) -> str:
    """``|x|`` with ``p`` digits after the point."""
    return "%.*f" % (p, abs(x))


def _exp_digits(x: float, p: int) -> tuple[str, int]:
    """``|x|`` as ``p + 1`` significant digits and the power of ten of the first."""
    s = "%.*e" % (p, abs(x))
    mant, _, exp = s.partition("e")
    return mant.replace(".", ""), int(exp)


def _exp_form(digits: str, exp: int, p: int, alt: bool, upper: bool) -> str:
    mant = f"{digits[0]}.{digits[1:]}" if p > 0 or alt else digits
    sign = "-" if exp < 0 else "+"
    return f"{mant}{'E' if upper else 'e'}{sign}{abs(exp):02d}"


def _strip_zeros(s: str) -> str:
    if "." not in s:
        return s
    return s.rstrip("0").rstrip(".")


def _float_body(x: float, conv: str, prec: int | None, alt: bool) -> str:
    """A float conversion's body (no sign): ``e E f F g G``."""
    upper = conv in ("E", "F", "G")
    if not math.isfinite(x):
        word = "nan" if math.isnan(x) else "inf"
        return word.upper() if upper else word
    lower = conv.lower()
    if lower == "f":
        p = 6 if prec is None else prec
        body = _fixed_digits(x, p)
        return body + "." if alt and p == 0 else body
    if lower == "e":
        p = 6 if prec is None else prec
        digits, exp = _exp_digits(x, p)
        return _exp_form(digits, exp, p, alt, upper)
    # g: %e's exponent picks the style; trailing zeros go unless ``#``
    big_p = 6 if prec is None else (1 if prec == 0 else prec)
    digits, exp = _exp_digits(x, big_p - 1)
    if exp < -4 or exp >= big_p:
        body = _exp_form(digits, exp, big_p - 1, alt, upper)
    else:
        body = _fixed_digits(x, big_p - 1 - exp)
        if alt and "." not in body:
            body += "."
    if alt:
        return body
    if exp < -4 or exp >= big_p:
        at = body.lower().index("e")
        return _strip_zeros(body[:at]) + body[at:]
    return _strip_zeros(body)


def _int_body(n: float, conv: str, prec: int | None, alt: bool) -> str:
    """An integer conversion's body (no sign): ``d i u o x X``. A negative
    number under ``o u x X`` is read as unsigned, 64 bits, as C reads it."""
    if not math.isfinite(n):
        return "nan" if math.isnan(n) else "inf"
    whole = int(n)
    mag = abs(whole) if conv in ("d", "i") else whole % (1 << 64)
    if conv == "o":
        digits = format(mag, "o")
    elif conv == "x":
        digits = format(mag, "x")
    elif conv == "X":
        digits = format(mag, "X")
    else:
        digits = str(mag)
    if prec is not None:
        digits = "" if prec == 0 and mag == 0 else digits.rjust(prec, "0")
    if alt and conv == "o" and not digits.startswith("0"):
        digits = "0" + digits
    return digits


_DIGIT = re.compile(r"[0-9]")


def _format_printf(fmt: str, args: list[Val], convfmt: str = "%.6g") -> str:
    """``printf``'s and ``sprintf``'s formatting of ``args`` by ``fmt``."""
    out: list[str] = []
    argi = 0

    def next_arg() -> Val:
        nonlocal argi
        if argi >= len(args):
            raise AwkRuntimeError(
                f"awk: not enough arguments for the format {_js_quote(fmt)}"
            )
        v = args[argi]
        argi += 1
        return v

    n_fmt = len(fmt)

    def at(k: int) -> str:
        return fmt[k] if k < n_fmt else ""

    i = 0
    while i < n_fmt:
        ch = fmt[i]
        if ch != "%":
            out.append(ch)
            i += 1
            continue
        start = i
        i += 1
        if at(i) == "%":
            out.append("%")
            i += 1
            continue
        flags = ""
        while i < n_fmt and fmt[i] in "-+ #0":
            flags += fmt[i]
            i += 1
        width: int | None = None
        if at(i) == "*":
            i += 1
            width = int(_trunc(_to_num(next_arg())))
            if width < 0:
                flags += "-"
                width = -width
        else:
            w = ""
            while _DIGIT.fullmatch(at(i) or "x"):
                w += fmt[i]
                i += 1
            if w:
                width = int(w)
        prec: int | None = None
        if at(i) == ".":
            i += 1
            if at(i) == "*":
                i += 1
                p = int(_trunc(_to_num(next_arg())))
                prec = None if p < 0 else p
            else:
                p_text = ""
                while _DIGIT.fullmatch(at(i) or "x"):
                    p_text += fmt[i]
                    i += 1
                prec = int(p_text) if p_text else 0
        conv = at(i)
        if conv == "" or conv not in "diouxXeEfFgGcs":
            # not a conversion: kept as written
            out.append(fmt[start:i])
            continue
        i += 1
        left = "-" in flags

        def pad(body: str, sign: str, zero: bool) -> str:
            room = 0 if width is None else width - len(sign) - len(body)
            if room <= 0:
                return sign + body
            if left:
                return sign + body + " " * room
            if zero:
                return sign + "0" * room + body
            return " " * room + sign + body

        # ``0`` pads strings and characters with zeros too, as C's printf does
        zero_text = "0" in flags and not left
        if conv == "s":
            s = _val_to_str(next_arg(), convfmt)
            if prec is not None:
                s = s[:prec]
            out.append(pad(s, "", zero_text))
            continue
        if conv == "c":
            v = next_arg()
            if isinstance(v, float) or (isinstance(v, StrNum) and _looks_numeric(v)):
                code = _trunc(_to_num(v))
                ok = 0 <= code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF
                s = chr(int(code)) if ok else ""
            else:
                s = _val_to_str(v, convfmt)[:1]
            out.append(pad(s, "", zero_text))
            continue
        n = _to_num(next_arg())
        plus = "+" if "+" in flags else (" " if " " in flags else "")
        alt = "#" in flags
        if conv in "diouxX":
            signed = conv in ("d", "i")
            negative = n <= -1 if signed else n == -math.inf
            int_sign = (
                "-" if negative else (plus if signed and not math.isnan(n) else "")
            )
            body = _int_body(n, conv, prec, alt)
            prefix = ""
            if alt and conv in ("x", "X") and math.isfinite(n) and _trunc(abs(n)) != 0:
                prefix = "0x" if conv == "x" else "0X"
            zero = "0" in flags and not left and prec is None and math.isfinite(n)
            if zero and width is not None:
                body = body.rjust(width - len(int_sign) - len(prefix), "0")
            out.append(pad(prefix + body, int_sign, False))
            continue
        body = _float_body(n, conv, prec, alt)
        sign = "-" if n < 0 or (n == 0 and math.copysign(1, n) < 0) else plus
        out.append(pad(body, sign, "0" in flags and not left and math.isfinite(n)))
    return "".join(out)


def _trunc(n: float) -> float:
    return float(math.trunc(n)) if math.isfinite(n) else n


def _js_quote(s: str) -> str:
    """``s`` quoted as ``JSON.stringify`` quotes it, as termish-ts reports it."""
    return json.dumps(s, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

_POSIX_CLASSES = {
    "alpha": "A-Za-z",
    "digit": "0-9",
    "alnum": "A-Za-z0-9",
    "upper": "A-Z",
    "lower": "a-z",
    "space": " \\t\\n\\r\\f\\v",
    "blank": " \\t",
    "punct": "!-\\/:-@\\[-`{-~",
    "xdigit": "0-9A-Fa-f",
    "cntrl": "\\x00-\\x1f\\x7f",
    "print": "\\x20-\\x7e",
    "graph": "\\x21-\\x7e",
}


def _ere_to_py(src: str) -> str:
    """An awk ERE as a Python regex: POSIX classes such as ``[[:digit:]]``
    read as their ranges, and ``$`` anchors only at the very end, as in
    awk (Python's also matches before a final newline)."""
    out: list[str] = []
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        if ch == "\\" and i + 1 < n:
            out.append(src[i : i + 2])
            i += 2
            continue
        if ch == "$":
            out.append(r"\Z")
            i += 1
            continue
        if ch != "[":
            out.append(ch)
            i += 1
            continue
        out.append("[")
        i += 1
        if src[i : i + 1] == "^":
            out.append("^")
            i += 1
        if src[i : i + 1] == "]":
            out.append("\\]")
            i += 1
        while i < n and src[i] != "]":
            c = src[i]
            if c == "[" and src[i + 1 : i + 2] == ":":
                end = src.find(":]", i + 2)
                name = src[i + 2 : end] if end != -1 else ""
                if name in _POSIX_CLASSES:
                    out.append(_POSIX_CLASSES[name])
                    i = end + 2
                    continue
            if c == "\\" and i + 1 < n:
                out.append(src[i : i + 2])
                i += 2
                continue
            out.append("\\[" if c == "[" else c)
            i += 1
        if i < n:
            out.append("]")
            i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------


@dataclass
class _Output:
    base: str  # truncate | append
    text: list[str] = field(default_factory=list)
    open: bool = True


class _Interp:
    def __init__(self, ctx: CommandContext) -> None:
        self._ctx = ctx
        self._vars: dict[str, Val | AwkArray] = {}
        self._regexes: dict[str, re.Pattern[str]] = {}
        self._record = ""
        self._fields: list[str] = []
        self._out: list[str] = []
        self._err: list[str] = []
        self._files: dict[str, _Output] = {}
        self._in_begin = False
        self._in_end = False
        defaults: dict[str, Val] = {
            "FS": " ",
            "OFS": " ",
            "ORS": "\n",
            "RS": "\n",
            "NR": 0.0,
            "FNR": 0.0,
            "FILENAME": "",
            "SUBSEP": "\x1c",
            "RSTART": 0.0,
            "RLENGTH": -1.0,
            "CONVFMT": "%.6g",
            "OFMT": "%.6g",
        }
        self._vars.update(defaults)
        self._vars["ENVIRON"] = {k: StrNum(v) for k, v in ctx.env.items()}

    # -- variables ----------------------------------------------------------

    def _scalar(self, name: str) -> Val:
        if name == "NF":
            return float(len(self._fields))
        v = self._vars.get(name)
        if v is None:
            return UNINIT
        if isinstance(v, dict):
            raise AwkRuntimeError(f"awk: can't use array {name} in a scalar context")
        return v

    def set_scalar(self, name: str, v: Val) -> None:
        if name == "NF":
            self._set_nf(int(_trunc(_to_num(v))))
            return
        if isinstance(self._vars.get(name), dict):
            raise AwkRuntimeError(f"awk: can't assign to {name}: it's an array")
        self._vars[name] = v

    def array(self, name: str) -> AwkArray:
        v = self._vars.get(name)
        if isinstance(v, dict):
            return v
        if v is not None and v is not UNINIT:
            raise AwkRuntimeError(f"awk: can't use scalar {name} as an array")
        arr: AwkArray = {}
        self._vars[name] = arr
        return arr

    def _str(self, v: Val) -> str:
        return _val_to_str(v, self._convfmt())

    def _convfmt(self) -> str:
        return self._str2(self._scalar("CONVFMT"))

    def _str2(self, v: Val) -> str:
        """A special variable's text, without recursing through CONVFMT."""
        return _val_to_str(v, "%.6g")

    def _out_str(self, v: Val) -> str:
        if isinstance(v, float):
            return _num_to_str(v, self._str2(self._scalar("OFMT")))
        return self._str(v)

    def _key(self, subs: list[Expr]) -> str:
        if len(subs) == 1:
            return self._str(self.eval(subs[0]))
        sep = self._str2(self._scalar("SUBSEP"))
        return sep.join(self._str(self.eval(s)) for s in subs)

    # -- records and fields -------------------------------------------------

    def set_record(self, text: str) -> None:
        self._record = text
        self._fields = self.split(text, self._scalar("FS"))

    def _set_nf(self, n: int) -> None:
        if n < 0:
            raise AwkRuntimeError("awk: NF set to a negative value")
        if n < len(self._fields):
            self._fields = self._fields[:n]
        while len(self._fields) < n:
            self._fields.append("")
        self._rebuild()

    def _rebuild(self) -> None:
        self._record = self._str2(self._scalar("OFS")).join(self._fields)

    def _get_field(self, i: int) -> Val:
        if i < 0:
            raise AwkRuntimeError(f"awk: trying to access out of range field {i}")
        if i == 0:
            return StrNum(self._record)
        if i > len(self._fields):
            return UNINIT
        return StrNum(self._fields[i - 1])

    def _set_field(self, i: int, v: Val) -> None:
        if i < 0:
            raise AwkRuntimeError(f"awk: trying to access out of range field {i}")
        text = self._str(v)
        if i == 0:
            self.set_record(text)
            return
        while len(self._fields) < i:
            self._fields.append("")
        self._fields[i - 1] = text
        self._rebuild()

    def split(self, s: str, sep: Val | re.Pattern[str]) -> list[str]:
        """``s`` split as fields are: on runs of blanks for " ", on a single
        character literally, into characters for "", on a regex otherwise."""
        if s == "":
            return []
        if isinstance(sep, re.Pattern):
            return self._split_regex(s, sep)
        fs = self._str(sep)
        if fs == " ":
            parts = re.split(r"[ \t\n]+", s)
            if parts and parts[0] == "":
                parts = parts[1:]
            if parts and parts[-1] == "":
                parts = parts[:-1]
            return parts
        if fs == "":
            return list(s)
        if len(fs) == 1 and fs != "\\":
            return s.split(fs)
        return self._split_regex(s, self._regex(fs))

    def _split_regex(self, s: str, pattern: re.Pattern[str]) -> list[str]:
        out: list[str] = []
        pos = 0
        frm = 0
        while frm <= len(s):
            m = pattern.search(s, frm)
            if m is None:
                break
            if m.end() == m.start():
                frm = m.start() + 1
                continue
            out.append(s[pos : m.start()])
            pos = m.end()
            frm = pos
        out.append(s[pos:])
        return out

    # -- regexes ------------------------------------------------------------

    def _regex(self, src: str) -> re.Pattern[str]:
        pattern = self._regexes.get(src)
        if pattern is None:
            try:
                pattern = re.compile(_ere_to_py(src), re.ASCII)
            except re.error:
                raise AwkRuntimeError(f"awk: invalid regex: /{src}/") from None
            self._regexes[src] = pattern
        return pattern

    def _regex_of(self, e: Expr) -> re.Pattern[str]:
        """A regex argument: a ``/re/`` literal as itself, anything else as
        the text of one."""
        if e[0] == "ere":
            return self._regex(e[1])
        return self._regex(self._str(self.eval(e)))

    # -- lvalues ------------------------------------------------------------

    def _set(self, target: Expr, v: Val) -> None:
        kind = target[0]
        if kind == "var":
            self.set_scalar(target[1], v)
        elif kind == "idx":
            self.array(target[1])[self._key(target[2])] = v
        elif kind == "field":
            self._set_field(int(_trunc(_to_num(self.eval(target[1])))), v)

    # -- expressions --------------------------------------------------------

    def eval(self, e: Expr) -> Val:
        t = e[0]
        if t == "num":
            return e[1]
        if t == "str":
            return e[1]
        if t == "ere":
            return 1.0 if self._regex(e[1]).search(self._record) else 0.0
        if t == "group":
            return self.eval(e[1])
        if t == "var":
            return self._scalar(e[1])
        if t == "idx":
            arr = self.array(e[1])
            key = self._key(e[2])
            if key in arr:
                return arr[key]
            arr[key] = UNINIT
            return UNINIT
        if t == "field":
            return self._get_field(int(_trunc(_to_num(self.eval(e[1])))))
        if t == "assign":
            _, op, target, rhs_e = e
            if op == "=":
                v = self.eval(rhs_e)
                self._set(target, v)
                return v
            rhs = _to_num(self.eval(rhs_e))
            n = _arith(op[:-1], _to_num(self.eval(target)), rhs)
            self._set(target, n)
            return n
        if t == "cond":
            return self.eval(e[2]) if _is_true(self.eval(e[1])) else self.eval(e[3])
        if t == "and":
            return (
                1.0 if _is_true(self.eval(e[1])) and _is_true(self.eval(e[2])) else 0.0
            )
        if t == "or":
            return (
                1.0 if _is_true(self.eval(e[1])) or _is_true(self.eval(e[2])) else 0.0
            )
        if t == "in":
            key = self._key(e[1])
            return 1.0 if key in self.array(e[2]) else 0.0
        if t == "match":
            _, neg, a, re_e = e
            s = self._str(self.eval(a))
            hit = self._regex_of(re_e).search(s) is not None
            return 1.0 if hit != neg else 0.0
        if t == "cmp":
            return 1.0 if self._compare(e[1], self.eval(e[2]), self.eval(e[3])) else 0.0
        if t == "concat":
            return self._str(self.eval(e[1])) + self._str(self.eval(e[2]))
        if t == "bin":
            return _arith(e[1], _to_num(self.eval(e[2])), _to_num(self.eval(e[3])))
        if t == "neg":
            return -_to_num(self.eval(e[1]))
        if t == "pos":
            return _to_num(self.eval(e[1]))
        if t == "not":
            return 0.0 if _is_true(self.eval(e[1])) else 1.0
        if t == "incr":
            _, op, prefix, target = e
            old = _to_num(self.eval(target))
            now = old + 1 if op == "++" else old - 1
            self._set(target, now)
            return now if prefix else old
        if t == "call":
            return self._call(e[1], e[2])
        raise AssertionError(t)

    def _compare(self, op: str, a: Val, b: Val) -> bool:
        if _looks_numeric(a) and _looks_numeric(b):
            x: float | str = _to_num(a)
            y: float | str = _to_num(b)
        else:
            x = self._str(a)
            y = self._str(b)
        if x < y:  # type: ignore[operator]
            c = -1.0
        elif x > y:  # type: ignore[operator]
            c = 1.0
        elif x == y:
            c = 0.0
        else:
            c = math.nan
        if op == "<":
            return c < 0
        if op == "<=":
            return c <= 0
        if op == "==":
            return c == 0
        if op == "!=":
            return c != 0
        if op == ">=":
            return c >= 0
        return c > 0

    def _call(self, name: str, args: list[Expr]) -> Val:
        def arg(i: int) -> Val:
            return self.eval(args[i])

        def num(i: int) -> float:
            return _to_num(arg(i))

        if name == "length":
            if not args:
                return float(len(self._record))
            a = args[0]
            if a[0] == "var" and isinstance(self._vars.get(a[1]), dict):
                return float(len(self._vars[a[1]]))  # type: ignore[arg-type]
            return float(len(self._str(arg(0))))
        if name == "substr":
            s = self._str(arg(0))
            m = _trunc(num(1))
            start = max(m, 1.0)
            end = float(len(s) + 1)
            if len(args) == 3:
                end = min(end, m + _trunc(num(2)))
            if not end > start:
                return ""
            return s[int(start) - 1 : int(end) - 1]
        if name == "index":
            s = self._str(arg(0))
            return float(s.find(self._str(arg(1))) + 1)
        if name == "split":
            s = self._str(arg(0))
            target = self.array(args[1][1])
            sep: Val | re.Pattern[str] = self._scalar("FS")
            if len(args) == 3:
                a = args[2]
                sep = self._regex(a[1]) if a[0] == "ere" else self.eval(a)
            parts = self.split(s, sep)
            target.clear()
            for i, p in enumerate(parts):
                target[str(i + 1)] = StrNum(p)
            return float(len(parts))
        if name in ("sub", "gsub"):
            pattern = self._regex_of(args[0])
            repl = self._str(arg(1))
            tgt: Expr = args[2] if len(args) == 3 else ("field", ("num", 0.0))
            text, count = _substitute(
                pattern, repl, self._str(self.eval(tgt)), name == "gsub"
            )
            if count > 0:
                self._set(tgt, text)
            return float(count)
        if name == "match":
            s = self._str(arg(0))
            m = self._regex_of(args[1]).search(s)
            start = 0.0 if m is None else float(m.start() + 1)
            self._vars["RSTART"] = start
            self._vars["RLENGTH"] = -1.0 if m is None else float(m.end() - m.start())
            return start
        if name == "sprintf":
            return _format_printf(
                self._str(arg(0)), [self.eval(a) for a in args[1:]], self._convfmt()
            )
        if name == "tolower":
            return self._str(arg(0)).lower()
        if name == "toupper":
            return self._str(arg(0)).upper()
        if name == "int":
            return _trunc(num(0))
        if name == "sqrt":
            x = num(0)
            return math.sqrt(x) if x >= 0 else math.nan
        if name == "exp":
            try:
                return math.exp(num(0))
            except OverflowError:
                return math.inf
        if name == "log":
            x = num(0)
            if x > 0 or math.isnan(x):
                return math.log(x)
            return -math.inf if x == 0 else math.nan
        if name in ("sin", "cos"):
            x = num(0)
            if not math.isfinite(x):
                return math.nan
            return math.sin(x) if name == "sin" else math.cos(x)
        if name == "atan2":
            return math.atan2(num(0), num(1))
        if name == "close":
            out = self._files.get(self._str(arg(0)))
            if out is None or not out.open:
                return -1.0
            out.open = False
            return 0.0
        if name == "fflush":
            return 0.0
        raise AwkRuntimeError(f"awk: {name}() is not supported")

    # -- statements ---------------------------------------------------------

    def exec(self, s: Stmt) -> None:
        t = s[0]
        if t == "block":
            for b in s[1]:
                self.exec(b)
            return
        if t == "expr":
            self.eval(s[1])
            return
        if t == "print":
            _, args, dest = s
            if not args:
                text = self._record
            else:
                ofs = self._str2(self._scalar("OFS"))
                text = ofs.join(self._out_str(self.eval(a)) for a in args)
            self._emit(dest, text + self._str2(self._scalar("ORS")))
            return
        if t == "printf":
            _, args, dest = s
            vals = [self.eval(a) for a in args]
            self._emit(
                dest, _format_printf(self._str(vals[0]), vals[1:], self._convfmt())
            )
            return
        if t == "if":
            _, c, then, other = s
            if _is_true(self.eval(c)):
                self.exec(then)
            elif other is not None:
                self.exec(other)
            return
        if t == "while":
            while _is_true(self.eval(s[1])):
                if self._loop_body(s[2]):
                    break
            return
        if t == "do":
            while True:
                if self._loop_body(s[1]):
                    break
                if not _is_true(self.eval(s[2])):
                    break
            return
        if t == "for":
            _, init, c, step, body = s
            if init is not None:
                self.eval(init)
            while c is None or _is_true(self.eval(c)):
                if self._loop_body(body):
                    break
                if step is not None:
                    self.eval(step)
            return
        if t == "forin":
            _, name, arr_name, body = s
            arr = self.array(arr_name)
            for key in list(arr.keys()):
                if key not in arr:
                    continue
                self.set_scalar(name, key)
                if self._loop_body(body):
                    break
            return
        if t == "break":
            raise _Break()
        if t == "continue":
            raise _Continue()
        if t == "next":
            if self._in_begin or self._in_end:
                raise AwkRuntimeError("awk: next used in BEGIN or END")
            raise _Next()
        if t == "exit":
            e = s[1]
            raise _Exit(None if e is None else int(_trunc(_to_num(self.eval(e)))))
        if t == "delete":
            _, name, subs = s
            arr = self.array(name)
            if subs is None:
                arr.clear()
            else:
                arr.pop(self._key(subs), None)
            return
        raise AssertionError(t)

    def _loop_body(self, body: Stmt) -> bool:
        """Run a loop's body: whether it broke out."""
        try:
            self.exec(body)
        except _Break:
            return True
        except _Continue:
            return False
        return False

    def _emit(self, dest: tuple | None, text: str) -> None:
        if dest is None:
            self._out.append(text)
            return
        mode, path_e = dest
        path = self._str(self.eval(path_e))
        if path in ("/dev/stdout", "-"):
            self._out.append(text)
            return
        if path == "/dev/stderr":
            self._err.append(text)
            return
        out = self._files.get(path)
        if out is None:
            out = _Output("truncate" if mode == ">" else "append")
            self._files[path] = out
        elif not out.open:
            # closed, then opened again: ``>`` starts the file over
            if mode == ">":
                out.base = "truncate"
                out.text = []
            out.open = True
        out.text.append(text)

    # -- the run ------------------------------------------------------------

    def run(self, items: list[Item], operands: list[str], assigns: list[str]) -> int:
        for a in assigns:
            self._assign_arg(a)
        argv: AwkArray = {"0": "awk"}
        for i, o in enumerate(operands):
            argv[str(i + 1)] = StrNum(o)
        self._vars["ARGV"] = argv
        self._vars["ARGC"] = float(len(operands) + 1)

        begins = [i for i in items if i.kind == "begin"]
        mains = [i for i in items if i.kind == "main"]
        ends = [i for i in items if i.kind == "end"]
        code: int | None = None

        self._in_begin = True
        try:
            for item in begins:
                self.exec(item.action)  # type: ignore[arg-type]
        except _Exit as e:
            code = e.code if e.code is not None else 0
        self._in_begin = False

        if code is None and (mains or ends):
            try:
                self._read_input(mains, operands)
            except _Exit as e:
                code = e.code if e.code is not None else 0

        self._in_end = True
        try:
            for item in ends:
                self.exec(item.action)  # type: ignore[arg-type]
        except _Exit as e:
            if e.code is not None:
                code = e.code
        return code if code is not None else 0

    def _assign_arg(self, a: str) -> None:
        """``var=value`` from ``-v`` or a file operand."""
        name, _, value = a.partition("=")
        self.set_scalar(name, StrNum(read_escapes(value)))

    def _read_input(self, mains: list[Item], operands: list[str]) -> None:
        # with no file named, stdin is read after the assignments
        files = [o for o in operands if not _is_assignment(o)]
        sources = operands + ["-"] if not files else operands
        for src in sources:
            if _is_assignment(src):
                self._assign_arg(src)
                continue
            if src in ("-", "/dev/stdin"):
                text = self._ctx.stdin.read()
            else:
                try:
                    data = self._ctx.fs.read(src)
                except OSError as e:
                    raise AwkRuntimeError(
                        f"awk: can't open file {src}: {_describe(e)}"
                    ) from None
                text = data.decode("utf-8", errors="replace")
            self._vars["FILENAME"] = "" if src == "-" else src
            self._vars["FNR"] = 0.0
            for record in self._records(text):
                self._vars["NR"] = _to_num(self._scalar("NR")) + 1
                self._vars["FNR"] = _to_num(self._scalar("FNR")) + 1
                self.set_record(record)
                try:
                    for item in mains:
                        if not self._matches(item):
                            continue
                        if item.action is None:
                            self._out.append(
                                self._record + self._str2(self._scalar("ORS"))
                            )
                        else:
                            self.exec(item.action)
                except _Next:
                    pass

    def _records(self, text: str) -> Iterator[str]:
        rs = self._str2(self._scalar("RS"))
        if rs == "":
            # paragraph mode: records are separated by blank lines
            paras = re.split(r"\n\n+", re.sub(r"^\n+", "", text))
            if paras and re.sub(r"\n+$", "", paras[-1]) == "":
                paras.pop()
            for p in paras:
                yield re.sub(r"\n+$", "", p)
            return
        parts = text.split(rs) if len(rs) == 1 else self._split_all(text, rs)
        if parts and parts[-1] == "":
            parts.pop()
        yield from parts

    def _split_all(self, text: str, rs: str) -> list[str]:
        return self._regex(rs).split(text)

    def _matches(self, item: Item) -> bool:
        if item.pattern is None:
            return True
        if item.until is None:
            return _is_true(self.eval(item.pattern))
        if not item.in_range:
            if not _is_true(self.eval(item.pattern)):
                return False
            item.in_range = True
        if _is_true(self.eval(item.until)):
            item.in_range = False
        return True

    def flush(self) -> None:
        """Write what the run printed: stdout, then the files it printed to."""
        if self._out:
            self._ctx.stdout.write("".join(self._out))
        self._out = []
        for path, out in self._files.items():
            try:
                self._ctx.fs.write(
                    path,
                    "".join(out.text).encode("utf-8"),
                    "w" if out.base == "truncate" else "a",
                )
            except OSError as e:
                raise AwkRuntimeError(
                    f"awk: can't write to {path}: {_describe(e)}"
                ) from None
        self._files = {}

    @property
    def stderr(self) -> str:
        return "".join(self._err)


def _describe(e: OSError) -> str:
    if isinstance(e, FileNotFoundError):
        return "No such file or directory"
    if isinstance(e, IsADirectoryError):
        return "Is a directory"
    if isinstance(e, NotADirectoryError):
        return "Not a directory"
    if isinstance(e, PermissionError):
        return "Permission denied"
    return str(e)


def _arith(op: str, a: float, b: float) -> float:
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        if b == 0:
            raise AwkRuntimeError("awk: division by zero")
        return a / b
    if op == "%":
        if b == 0:
            raise AwkRuntimeError("awk: division by zero in %")
        return math.fmod(a, b)
    return _pow(a, b)


def _pow(a: float, b: float) -> float:
    """``a ** b`` as JavaScript computes it, so the twins agree."""
    if math.isnan(b) or (abs(a) == 1 and math.isinf(b)):
        return math.nan if not (b == 0) else 1.0
    odd = b.is_integer() and math.isfinite(b) and int(b) % 2 == 1
    try:
        return math.pow(a, b)
    except ZeroDivisionError:
        return math.copysign(math.inf, a) if odd else math.inf
    except ValueError:
        if a == 0 and b < 0:
            return math.copysign(math.inf, a) if odd else math.inf
        return math.nan
    except OverflowError:
        return -math.inf if a < 0 and odd else math.inf


def _substitute(
    pattern: re.Pattern[str], repl: str, s: str, global_: bool
) -> tuple[str, int]:
    """``sub``/``gsub`` on ``s``: the text after, and how many matches were
    replaced. In ``repl``, ``&`` is the match and ``\\&`` a literal ``&``; an
    empty match right after a replaced one is skipped, as awk skips it."""
    out: list[str] = []
    pos = 0
    count = 0
    last_end = -1
    n = len(s)
    while pos <= n:
        m = pattern.search(s, pos)
        if m is None:
            break
        at = m.start()
        length = m.end() - m.start()
        if length == 0 and at == last_end:
            if at >= n:
                break
            out.append(s[at])
            pos = at + 1
            continue
        out.append(s[pos:at] + _expand_repl(repl, m.group(0)))
        count += 1
        last_end = at + length
        if length == 0:
            if at < n:
                out.append(s[at])
            pos = at + 1
        else:
            pos = at + length
        if not global_:
            break
    if pos <= n:
        out.append(s[pos:])
    return "".join(out), count


def _expand_repl(repl: str, matched: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(repl):
        c = repl[i]
        if c == "\\" and repl[i + 1 : i + 2] in ("&", "\\"):
            out.append(repl[i + 1])
            i += 2
            continue
        out.append(matched if c == "&" else c)
        i += 1
    return "".join(out)


_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")


def _is_assignment(operand: str) -> bool:
    return _ASSIGNMENT.match(operand) is not None


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------

USAGE = "usage: awk [-F fs][-v var=value][prog | -f progfile][file ...]"


def awk(ctx: CommandContext) -> CommandResult | None:
    """``awk [-F fs] [-v var=value]... ('program' | -f progfile) [file | var=value]...``"""
    assigns: list[str] = []
    prog_files: list[str] = []
    fs: str | None = None
    args = ctx.args
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            break
        if a == "-" or not a.startswith("-"):
            break
        flag = a[:2]
        inline = a[2:]
        if flag not in ("-F", "-v", "-f"):
            return CommandResult(
                exit_code=2, stderr=f"awk: unknown option {a}\n{USAGE}"
            )
        if inline != "":
            value = inline
        else:
            if i + 1 >= len(args):
                return CommandResult(
                    exit_code=2, stderr=f"awk: {flag} needs a value\n{USAGE}"
                )
            i += 1
            value = args[i]
        if flag == "-F":
            fs = "\t" if value == "t" else read_escapes(value)
        elif flag == "-v":
            if not _is_assignment(value):
                return CommandResult(
                    exit_code=2, stderr=f"awk: -v needs var=value, not '{value}'"
                )
            assigns.append(value)
        else:
            prog_files.append(value)
        i += 1

    if prog_files:
        texts: list[str] = []
        for p in prog_files:
            try:
                texts.append(ctx.fs.read(p).decode("utf-8", errors="replace"))
            except OSError as e:
                return CommandResult(
                    exit_code=2,
                    stderr=f"awk: can't open program file {p}: {_describe(e)}",
                )
        program = "\n".join(texts)
    else:
        if i >= len(args):
            return CommandResult(exit_code=2, stderr=USAGE)
        program = args[i]
        i += 1
    operands = list(args[i:])

    try:
        items = Parser(program).parse()
    except AwkSyntaxError as e:
        return CommandResult(exit_code=2, stderr=str(e))

    interp = _Interp(ctx)
    if fs is not None:
        interp.set_scalar("FS", fs)
    try:
        code = interp.run(items, operands, assigns)
        interp.flush()
    except AwkRuntimeError as e:
        try:
            interp.flush()
        except AwkRuntimeError:
            pass
        return CommandResult(exit_code=2, stderr=interp.stderr + str(e))
    if code != 0 or interp.stderr:
        return CommandResult(exit_code=code, stderr=interp.stderr)
    return None
