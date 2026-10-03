r"""POSIX basic regular expressions (BRE), read into Python ``re`` syntax.

``grep`` and ``sed`` without ``-E`` take BRE, the syntax agents write
when they write GNU commands, and its escaping is roughly the reverse of
Python's: ``\(``, ``\{``, ``\|``, ``\+`` and ``\?`` are operators while
their bare forms are literal characters. Handed to ``re`` unchanged, the
operators read as literals, so ``s/a\+/X/`` matched nothing and still
exited 0, and a bare ``(`` was a syntax error rather than a parenthesis.

With ``-E`` a pattern is already in the syntax ``re`` speaks and is
passed through untouched; this module is for the other case.
"""

from __future__ import annotations

#: Escaped in BRE they are operators; bare, they are literal characters.
_SWAPPED = "(){}|+?"


def bre_to_python(pattern: str) -> str:
    r"""Translate a POSIX BRE ``pattern`` into a Python ``re`` pattern.

    Outside a bracket expression:

    - ``\( \) \{ \} \| \+ \?`` become ``( ) { } | + ?``, and their bare
      forms become escaped literals.
    - ``*`` is literal where nothing precedes it: at the start of the
      pattern, of a group, or of an alternative (and after a leading
      ``^``).
    - ``^`` anchors only at those same starting points and ``$`` only at
      the end of the pattern, of a group or of an alternative; anywhere
      else each is a literal character.
    - ``\<`` and ``\>`` (GNU word boundaries) become ``\b``.
    - Every other escape — ``\1``-``\9``, ``\.``, ``\*``, ``\w`` — is
      kept as it is, since it means the same to ``re``.

    Inside ``[...]`` a backslash is a literal character, as in BRE, and
    a ``]`` right after ``[`` or ``[^`` is a member rather than the end.
    """
    out: list[str] = []
    i = 0
    n = len(pattern)
    # True where nothing precedes this position in the current
    # alternative: '*' is literal here and '^' anchors.
    at_start = True
    while i < n:
        ch = pattern[i]
        if ch == "[":
            i = _bracket(pattern, i, out)
            at_start = False
            continue
        if ch == "\\":
            if i + 1 >= n:
                out.append("\\\\")  # a trailing backslash is literal
                i += 1
                continue
            nxt = pattern[i + 1]
            i += 2
            if nxt in _SWAPPED:
                out.append(nxt)
                at_start = nxt in "(|"
            elif nxt in "<>":
                out.append(r"\b")
                at_start = False
            else:
                out.append("\\" + nxt)
                at_start = False
            continue
        i += 1
        if ch in _SWAPPED:
            out.append("\\" + ch)
            at_start = False
        elif ch == "*" and at_start:
            out.append(r"\*")
            at_start = False
        elif ch == "^":
            # An anchor leaves the position a start: '^*' is a literal '*'.
            out.append("^" if at_start else r"\^")
        elif ch == "$":
            rest = pattern[i:]
            ends = not rest or rest.startswith(("\\)", "\\|"))
            out.append("$" if ends else r"\$")
            at_start = False
        else:
            out.append(ch)
            at_start = False
    return "".join(out)


def _bracket(pattern: str, start: int, out: list[str]) -> int:
    """Copy the bracket expression at ``start`` into ``out``, with its
    backslashes made literal; return the position after it.

    An unterminated bracket is copied as far as it goes, for ``re`` to
    refuse in its own words.
    """
    n = len(pattern)
    out.append("[")
    i = start + 1
    if i < n and pattern[i] == "^":
        out.append("^")
        i += 1
    if i < n and pattern[i] == "]":
        out.append(r"\]")
        i += 1
    while i < n:
        ch = pattern[i]
        i += 1
        if ch == "]":
            out.append("]")
            return i
        out.append("\\\\" if ch == "\\" else ch)
    return i
