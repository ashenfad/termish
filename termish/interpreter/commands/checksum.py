"""``md5sum`` and ``sha256sum``: a digest per file, and ``-c`` to check a
list of them.

Mirrors termish-ts's ``checksum.ts``.
"""

import hashlib
import re
from collections.abc import Callable

from termish.context import CommandContext, CommandResult
from termish.errors import TerminalError

from ._fserr import fs_error_text

#: ``HASH  NAME`` (or ``HASH *NAME``), as the commands write them.
_LINE_RE = re.compile(r"([0-9a-fA-F]+) [ *](.+)")

Digest = Callable[[bytes], str]


def _md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_bytes(ctx: CommandContext, path: str) -> bytes | str:
    """A file's bytes (stdin's for ``-``), or why it can't be read."""
    if path == "-":
        return ctx.stdin.buffer.read()
    try:
        if ctx.fs.isdir(path):
            return "Is a directory"
        return ctx.fs.read(path)
    except OSError as e:
        return fs_error_text(e)


def _parse(prog: str, args: list[str]) -> tuple[bool, list[str]]:
    check = False
    files: list[str] = []
    rest = iter(args)
    for arg in rest:
        if arg == "--":
            files.extend(rest)
            break
        if arg in ("--check",):
            check = True
        elif arg in ("--binary", "--text"):
            pass
        elif arg.startswith("--"):
            raise TerminalError(f"{prog}: unknown option: {arg.split('=', 1)[0]}")
        elif arg.startswith("-") and arg != "-":
            for ch in arg[1:]:
                if ch == "c":
                    check = True
                elif ch not in "bt":
                    raise TerminalError(f"{prog}: unknown option: -{ch}")
        else:
            files.append(arg)
    return check, files or ["-"]


def _checksum_command(prog: str, digest: Digest, hex_length: int):
    def command(ctx: CommandContext) -> CommandResult | None:
        check, files = _parse(prog, ctx.args)
        if check:
            return _check(ctx, prog, digest, hex_length, files)
        errors: list[str] = []
        for f in files:
            data = _read_bytes(ctx, f)
            if isinstance(data, str):
                errors.append(f"{prog}: {f}: {data}")
                continue
            ctx.stdout.write(f"{digest(data)}  {f}\n")
        return CommandResult(exit_code=1, stderr="\n".join(errors)) if errors else None

    command.__name__ = prog
    command.__doc__ = (
        f"``{prog} [-c] [FILE...]`` — each file's digest, as ``HASH  NAME``."
    )
    return command


def _check(
    ctx: CommandContext, prog: str, digest: Digest, hex_length: int, lists: list[str]
) -> CommandResult | None:
    """``-c``: read ``HASH  NAME`` lines, and say whether each file still
    matches."""
    errors: list[str] = []
    mismatched = 0
    unreadable = 0
    for listed in lists:
        raw = _read_bytes(ctx, listed)
        if isinstance(raw, str):
            raise TerminalError(f"{prog}: {listed}: {raw}")
        well_formed = 0
        for line in raw.decode("utf-8", errors="replace").split("\n"):
            m = _LINE_RE.fullmatch(line.removesuffix("\r"))
            if m is None or len(m.group(1)) != hex_length:
                continue
            well_formed += 1
            name = m.group(2)
            data = _read_bytes(ctx, name)
            if isinstance(data, str):
                errors.append(f"{prog}: {name}: {data}")
                ctx.stdout.write(f"{name}: FAILED open or read\n")
                unreadable += 1
                continue
            ok = digest(data) == m.group(1).lower()
            if not ok:
                mismatched += 1
            ctx.stdout.write(f"{name}: {'OK' if ok else 'FAILED'}\n")
        if well_formed == 0:
            errors.append(
                f"{prog}: {listed}: no properly formatted checksum lines found"
            )
    if unreadable:
        errors.append(
            f"{prog}: WARNING: {unreadable} listed file{'' if unreadable == 1 else 's'}"
            " could not be read"
        )
    if mismatched:
        errors.append(
            f"{prog}: WARNING: {mismatched} computed checksum"
            f"{'' if mismatched == 1 else 's'} did NOT match"
        )
    return CommandResult(exit_code=1, stderr="\n".join(errors)) if errors else None


md5sum = _checksum_command("md5sum", _md5, 32)
sha256sum = _checksum_command("sha256sum", _sha256, 64)
