"""Pathname expansion, as bash does it.

An unquoted word holding ``*``, ``?`` or a ``[...]`` class is matched
against the filesystem one path segment at a time:

- ``*``, ``?`` and ``[...]`` match within a segment and never cross a
  ``/``: ``a/*`` names the entries directly under ``a``, directories
  included, and nothing below them;
- a name starting with ``.`` is matched only by a segment that itself
  starts with ``.``, as with bash's ``dotglob`` off;
- ``**`` matches zero or more directories (bash with ``globstar`` on);
- a trailing ``/`` matches directories only, and is kept;
- the matches keep the word's own form: a relative word expands to
  relative paths and an absolute one to absolute paths, sorted.

A word that matches nothing is left as it is (``nullglob`` off), which
the caller does by getting an empty list back.

It is done here, through the filesystem protocol's ``list``, ``isdir``
and ``exists``, rather than by asking the filesystem to glob: every
filesystem then expands the same way, whether or not its own ``glob``
treats ``*`` as crossing ``/`` or knows about directories it holds
only implicitly.
"""

from __future__ import annotations

import posixpath
from fnmatch import fnmatchcase
from typing import Any


def has_magic(word: str) -> bool:
    """Whether ``word`` is a pattern: it holds ``*`` or ``?``, or a
    ``[`` closed by a later ``]``."""
    if "*" in word or "?" in word:
        return True
    start = word.find("[")
    return start != -1 and word.find("]", start + 2) != -1


def expand(pattern: str, fs: Any) -> list[str]:
    """The paths ``pattern`` matches, sorted; empty when none do."""
    absolute = pattern.startswith("/")
    parts = pattern.split("/")
    if absolute:
        parts = parts[1:]
    trailing = len(parts) > 1 and parts[-1] == ""
    if trailing:
        parts = parts[:-1]
    parts = [p for p in parts if p != ""] if parts else []
    found: set[str] = set()
    _walk(fs, "/" if absolute else "", parts, 0, trailing, found)
    return sorted(found)


def _join(base: str, name: str) -> str:
    if base == "":
        return name
    if base.endswith("/"):
        return base + name
    return base + "/" + name


def _names(fs: Any, base: str) -> list[str]:
    try:
        entries = fs.list(base or ".")
    except Exception:
        return []
    return [posixpath.basename(str(e).rstrip("/")) for e in entries]


def _isdir(fs: Any, path: str) -> bool:
    try:
        return bool(fs.isdir(path or "."))
    except Exception:
        return False


def _exists(fs: Any, path: str) -> bool:
    try:
        return bool(fs.exists(path))
    except Exception:
        return False


def _walk(
    fs: Any, base: str, parts: list[str], i: int, trailing: bool, found: set[str]
) -> None:
    if i == len(parts):
        if trailing:
            if _isdir(fs, base):
                found.add(base if base.endswith("/") else base + "/")
        elif base:
            found.add(base)
        return
    part = parts[i]
    last = i == len(parts) - 1
    if part == "**":
        # zero directories here, then one more level and ** again
        _walk(fs, base, parts, i + 1, trailing, found)
        for name in _names(fs, base):
            if name.startswith("."):
                continue
            path = _join(base, name)
            if _isdir(fs, path):
                _walk(fs, path, parts, i, trailing, found)
        return
    if not has_magic(part):
        path = _join(base, part)
        if (last and _exists(fs, path)) or (not last and _isdir(fs, path)):
            _walk(fs, path, parts, i + 1, trailing, found)
        return
    for name in _names(fs, base):
        if name.startswith(".") and not part.startswith("."):
            continue
        if not fnmatchcase(name, part):
            continue
        path = _join(base, name)
        if last or _isdir(fs, path):
            _walk(fs, path, parts, i + 1, trailing, found)
