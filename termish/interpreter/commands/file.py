"""``file`` — minimal magic-byte sniffer.

For each path argument, write one line of the form ``<path>: <description>``
to stdout. Covers the handful of types that scripts (and agents) tend
to care about when verifying a downloaded blob is what its extension
claims (gzip, zip, tar, PDF, PNG, JPEG, ELF, HTML), with a
UTF-8/ASCII/binary fallback.

Not a libmagic re-implementation. No ``--mime`` / ``-b`` flags; no
symlink handling (the ``FileSystem`` protocol doesn't model symlinks).
"""

from termish.context import CommandContext, CommandResult
from termish.errors import TerminalError

from ._util import looks_like_binary

# How many leading bytes to read for classification. tar's ``ustar``
# magic lives at offset 257, so we need at least 262; 1024 gives
# headroom and is still tiny.
_SNIFF_LEN = 1024


def file_cmd(ctx: CommandContext) -> CommandResult | None:
    args, _stdin, stdout, fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs

    if not args:
        raise TerminalError("file: missing operand")

    for path in args:
        try:
            meta = fs.stat(path)
        except Exception as e:
            raise TerminalError(f"file: {path}: {e}")

        if meta.is_dir:
            stdout.write(f"{path}: directory\n")
            continue

        try:
            data = fs.read(path)
        except Exception as e:
            raise TerminalError(f"file: {path}: {e}")

        stdout.write(f"{path}: {_classify(data)}\n")

    return None


def _classify(data: bytes) -> str:
    """Classify a byte buffer using magic prefixes, then fall back to a
    text/binary sniff. Returned strings deliberately mirror the most
    recognizable shape of GNU ``file``'s output."""
    if not data:
        return "empty"

    head = data[:_SNIFF_LEN]

    # gzip: 1f 8b
    if head.startswith(b"\x1f\x8b"):
        return "gzip compressed data"

    # zip: PK\x03\x04 (local file header), PK\x05\x06 (empty),
    #      PK\x07\x08 (spanned).
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return "Zip archive data"

    # PDF: %PDF-
    if head.startswith(b"%PDF-"):
        return "PDF document"

    # PNG: 89 50 4e 47 0d 0a 1a 0a
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG image data"

    # JPEG: ff d8 ff
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG image data"

    # ELF: 7f 45 4c 46
    if head.startswith(b"\x7fELF"):
        return "ELF binary"

    # POSIX tar: ``ustar`` at offset 257.
    if len(head) >= 262 and head[257:262] == b"ustar":
        return "POSIX tar archive"

    # HTML: leading ``<!DOCTYPE html`` or ``<html`` (case-insensitive,
    # tolerating leading whitespace).
    ascii_prefix = _decode_ascii_prefix(head, 64).lstrip().lower()
    if ascii_prefix.startswith("<!doctype html") or ascii_prefix.startswith("<html"):
        return "HTML document"

    # No magic match → text/binary fallback.
    if looks_like_binary(data):
        return "data"

    # Scan the full buffer (not just ``head``) for both checks. Limiting
    # to 1024 bytes would (a) misclassify a long file as text when its
    # trailing content is not ASCII / not valid UTF-8, and (b) trip
    # ``UnicodeDecodeError`` whenever a multi-byte UTF-8 char straddles
    # the 1024-byte boundary (truncation, not real invalid UTF-8).
    # ``looks_like_binary`` still samples only the first 4KB by design.
    if all(b < 0x80 for b in data):
        return "ASCII text"

    try:
        data.decode("utf-8")
        return "UTF-8 Unicode text"
    except UnicodeDecodeError:
        # Not valid UTF-8 but didn't trip the binary heuristic — call it
        # "data" rather than misclassify as text.
        return "data"


def _decode_ascii_prefix(data: bytes, max_len: int) -> str:
    """Decode the leading printable-ASCII run as a string for the HTML
    sniff. Stops at the first non-ASCII byte; returns up to ``max_len`` chars."""
    out = []
    for b in data[:max_len]:
        if b >= 0x80:
            break
        out.append(chr(b))
    return "".join(out)
