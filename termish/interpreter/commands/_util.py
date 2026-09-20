"""Shared utilities for terminal commands."""

import posixpath

from termish.fs import FileSystem


def resolve_path(path: str, fs: FileSystem) -> str:
    """Resolve a relative path against the filesystem's CWD and normalize it."""
    if not path.startswith("/"):
        path = posixpath.join(fs.getcwd(), path)
    return posixpath.normpath(path)


def looks_like_binary(data: bytes) -> bool:
    """Binary-content sniff over the first 4KB.

    Returns True for blobs that almost certainly aren't text:

    - any NUL byte (0x00) — instant True; almost never appears in real text
    - C0 / DEL control chars (excluding TAB, LF, CR) above ~1% of the sample

    Plain UTF-8 text, JSON, source code, markdown, and CSVs all pass
    comfortably; PNG/JPEG/protobuf/sqlite/etc. trip the gate.
    """
    if not data:
        return False
    sample = data[:4096]
    suspect = 0
    for b in sample:
        if b == 0:
            return True
        # Control chars except TAB (0x09), LF (0x0A), CR (0x0D); plus DEL (0x7F).
        if (b < 0x20 and b not in (0x09, 0x0A, 0x0D)) or b == 0x7F:
            suspect += 1
    return suspect / len(sample) > 0.01


def split_lines(data: bytes) -> list[bytes]:
    """Split content into lines at b"\\n", keeping the newline on each.

    Splitting at the byte level leaves every other byte exactly as it
    was: decoding first would rewrite each undecodable byte, and a text
    split would also break lines at characters a shell never counts as
    line endings (a lone \\x0b or \\x0c, U+2028). Trailing content with
    no final newline is its own line; b"" has no lines at all.
    """
    if not data:
        return []
    lines = data.split(b"\n")
    last = lines.pop()
    result = [line + b"\n" for line in lines]
    if last:
        result.append(last)
    return result
