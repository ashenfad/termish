"""Bash brace expansion, on one quote-masked word.

``{a,b,c}`` is a list and ``{1..5}``, ``{a..e}``, ``{01..10}``,
``{1..10..2}`` a sequence, with whatever is written around them
repeated for each (``src/{lib,test}`` is ``src/lib src/test``); lists
nest (``{a,b{1,2}}``). It runs before variables and globs, as in bash.
A brace with neither a top-level comma nor a sequence inside stays
literal (``{}``, ``{x}``, so ``find -exec cmd {} \\;`` keeps its
``{}``), and so does ``${NAME}``. Quoted text is a placeholder by now,
so a brace inside quotes never expands, nor an escaped one (the parser
quotes it).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from termish.errors import TerminalError

#: The most words one word may expand to.
BRACE_LIMIT = 100_000


def expand_braces(word: str) -> list[str]:
    """``word``'s brace expansion: ``[word]`` when it has none."""
    if "{" not in word:
        return [word]
    out = _expand(word)
    if len(out) > BRACE_LIMIT:
        _too_many()
    return out


def _too_many() -> None:
    raise TerminalError(f"brace expansion: more than {BRACE_LIMIT} words")


def _expand(word: str) -> list[str]:
    found = _first_brace(word)
    if found is None:
        return [word]
    prefix = word[: found.start]
    tails = _expand(word[found.end + 1 :])
    out: list[str] = []
    for item in found.items:
        for head in [item] if found.sequence else _expand(item):
            for tail in tails:
                out.append(prefix + head + tail)
                if len(out) > BRACE_LIMIT:
                    _too_many()
    return out


@dataclass(frozen=True)
class _Brace:
    start: int
    end: int
    items: list[str]
    #: The items are a sequence's, already literal.
    sequence: bool


def _first_brace(word: str) -> _Brace | None:
    """The leftmost brace that expands: its span and its items."""
    start = word.find("{")
    while start >= 0:
        if not (start > 0 and word[start - 1] == "$"):
            end = _matching(word, start)
            if end >= 0:
                inner = word[start + 1 : end]
                items = _split_top(inner)
                if len(items) > 1:
                    return _Brace(start, end, items, False)
                seq = _sequence(inner)
                if seq is not None:
                    return _Brace(start, end, seq, True)
        start = word.find("{", start + 1)
    return None


def _matching(word: str, open_: int) -> int:
    """The index of the ``}`` closing the ``{`` at ``open_``, or -1."""
    depth = 0
    for i in range(open_, len(word)):
        if word[i] == "{":
            depth += 1
        elif word[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _split_top(inner: str) -> list[str]:
    """``inner`` split at its top-level commas."""
    items: list[str] = []
    depth = 0
    start = 0
    for i, c in enumerate(inner):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == "," and depth == 0:
            items.append(inner[start:i])
            start = i + 1
    items.append(inner[start:])
    return items


_NUMBERS = re.compile(r"(-?[0-9]+)\.\.(-?[0-9]+)(?:\.\.(-?[0-9]+))?")
_LETTERS = re.compile(r"([A-Za-z])\.\.([A-Za-z])(?:\.\.(-?[0-9]+))?")
_PADDED = re.compile(r"-?0[0-9]")


def _sequence(inner: str) -> list[str] | None:
    """A sequence's items, or None when ``inner`` isn't one. A step's
    sign is ignored, as in bash: the ends say which way it runs."""
    m = _NUMBERS.fullmatch(inner)
    if m is not None:
        a, b, s = m.groups()
        first, last = int(a), int(b)
        step = abs(int(s or 1)) or 1
        if abs(last - first) / step + 1 > BRACE_LIMIT:
            _too_many()
        # a leading zero on either end pads every item to the wider end
        width = max(len(a), len(b)) if _PADDED.match(a) or _PADDED.match(b) else 0

        def pad(n: int) -> str:
            digits = str(abs(n)).rjust(width - 1 if n < 0 else width, "0")
            return f"-{digits}" if n < 0 else digits

        return [pad(n) for n in _range(first, last, step)]
    m = _LETTERS.fullmatch(inner)
    if m is not None:
        a, b, s = m.groups()
        step = abs(int(s or 1)) or 1
        return [chr(c) for c in _range(ord(a), ord(b), step)]
    return None


def _range(first: int, last: int, step: int) -> range:
    if first <= last:
        return range(first, last + 1, step)
    return range(first, last - 1, -step)
