"""Filesystem inspection commands: ``du``, ``stat``, ``realpath``.

Mirrors termish-ts's ``fsinfo.ts``. Sizes are a file's bytes, not disk
blocks: the filesystems here have none, so ``du`` sums what the files
hold (GNU's ``--apparent-size``).
"""

import math
import posixpath
import re
from datetime import datetime, timezone

from termish.context import CommandContext, CommandResult
from termish.errors import TerminalError
from termish.fs import FileMetadata

from ._fserr import fs_error_text
from ._util import resolve_path
from .clock import strftime


def _failed(errors: list[str]) -> CommandResult | None:
    """A failure partway, reported once the rest has run."""
    return CommandResult(exit_code=1, stderr="\n".join(errors)) if errors else None


def _short_flags(
    prog: str, args: list[str], known: dict[str, str]
) -> tuple[set[str], list[str]]:
    """Boolean flags (``known`` maps each spelling to its name, short
    ones clustering) and the operands."""
    flags: set[str] = set()
    operands: list[str] = []
    rest = iter(args)
    for arg in rest:
        if arg == "--":
            operands.extend(rest)
            break
        if arg.startswith("--"):
            if arg not in known:
                raise TerminalError(f"{prog}: unknown option: {arg.split('=', 1)[0]}")
            flags.add(known[arg])
        elif arg.startswith("-") and arg != "-":
            for ch in arg[1:]:
                if f"-{ch}" not in known:
                    raise TerminalError(f"{prog}: unknown option: -{ch}")
                flags.add(known[f"-{ch}"])
        else:
            operands.append(arg)
    return flags, operands


# =============================================================================
# du
# =============================================================================


def du_human(size: int) -> str:
    """A size as ``du -h`` shows it: bytes below 1K, then one decimal
    below 10 and none above, rounded up (``1.5K``, ``12K``, ``1.0M``)."""
    if size < 1024:
        return str(size)
    units = ["K", "M", "G", "T", "P"]
    v = size / 1024
    u = 0
    while True:
        shown = math.ceil(v * 10) / 10 if v < 10 else math.ceil(v)
        if shown < 1024 or u == len(units) - 1:
            text = f"{shown:.1f}" if shown < 10 else str(int(shown))
            return f"{text}{units[u]}"
        v /= 1024
        u += 1


def du(ctx: CommandContext) -> CommandResult | None:
    """``du [-s] [-a] [-c] [-h] [PATH...]``

    Bytes under each directory (each subdirectory too), the deepest
    first; ``-a`` lists files as well, ``-s`` only each PATH's total,
    ``-c`` adds a grand total, ``-h`` shows ``12K``-style sizes. PATH
    defaults to ``.``. Sizes are file bytes, not disk blocks.
    """
    flags, paths = _short_flags(
        "du",
        ctx.args,
        {
            "-s": "summarize",
            "--summarize": "summarize",
            "-a": "all",
            "--all": "all",
            "-c": "total",
            "--total": "total",
            "-h": "human",
            "--human-readable": "human",
            "-b": "bytes",
            "--bytes": "bytes",
            "--apparent-size": "bytes",
        },
    )
    if "summarize" in flags and "all" in flags:
        raise TerminalError("du: cannot both summarize and show all entries")

    def show(n: int) -> str:
        return du_human(n) if "human" in flags else str(n)

    errors: list[str] = []
    grand = 0
    for path in paths or ["."]:
        try:
            meta = ctx.fs.stat(path)
        except OSError as e:
            errors.append(f"du: cannot access '{path}': {fs_error_text(e)}")
            continue
        if not meta.is_dir:
            ctx.stdout.write(f"{show(meta.size)}\t{path}\n")
            grand += meta.size
            continue
        base = "" if path == "/" else path.rstrip("/")
        children, dirs, files = _tree_sizes(ctx, path)
        lines: list[str] = []

        def visit(d: str) -> int:
            total = 0
            for child in children.get(d, []):
                full = child if d == "" else f"{d}/{child}"
                if full in dirs:
                    total += visit(full)
                else:
                    size = files.get(full, 0)
                    total += size
                    if "all" in flags:
                        lines.append(f"{show(size)}\t{base}/{full}")
            if d != "" and "summarize" not in flags:
                lines.append(f"{show(total)}\t{base}/{d}")
            return total

        total = visit("")
        lines.append(f"{show(total)}\t{path}")
        grand += total
        ctx.stdout.write("\n".join(lines) + "\n")
    if "total" in flags:
        ctx.stdout.write(f"{show(grand)}\ttotal\n")
    return _failed(errors)


def _tree_sizes(
    ctx: CommandContext, root: str
) -> tuple[dict[str, list[str]], set[str], dict[str, int]]:
    """Each directory's entries (relative paths, ``""`` the root),
    sorted; the directories; each file's size."""
    children: dict[str, list[str]] = {}
    dirs: set[str] = set()
    files: dict[str, int] = {}
    prefix = root if root.endswith("/") else root + "/"
    for name in sorted(ctx.fs.list(root, recursive=True)):
        meta = ctx.fs.stat(prefix + name)
        if meta.is_dir:
            dirs.add(name)
        else:
            files[name] = meta.size
        parent, _, leaf = name.rpartition("/")
        children.setdefault(parent, []).append(leaf)
    return children, dirs, files


# =============================================================================
# stat
# =============================================================================


def _when(iso: str) -> datetime | None:
    """A metadata timestamp (ISO 8601, UTC), or None when there is none."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(re.sub(r"Z$", "+00:00", iso))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _show_time(dt: datetime | None) -> str:
    return "-" if dt is None else strftime(dt, "%Y-%m-%d %H:%M:%S %z", False)


def _epoch(dt: datetime | None) -> str:
    return "-" if dt is None else str(int(dt.timestamp() // 1))


def _file_type(meta: FileMetadata) -> str:
    if meta.is_dir:
        return "directory"
    return "regular empty file" if meta.size == 0 else "regular file"


def _stat_format(fmt: str, name: str, meta: FileMetadata) -> str:
    """``stat -c``'s conversions: ``%n %s %F %y %Y %w %W %%``."""
    modified = _when(meta.modified_at)
    born = _when(meta.created_at)
    values = {
        "n": name,
        "s": str(meta.size),
        "F": _file_type(meta),
        "y": _show_time(modified),
        "Y": _epoch(modified),
        "w": _show_time(born),
        "W": _epoch(born),
        "%": "%",
    }
    return re.sub(
        r"%(.)", lambda m: values.get(m.group(1), m.group(0)), fmt, flags=re.S
    )


def stat(ctx: CommandContext) -> CommandResult | None:
    """``stat [-c FORMAT] PATH...``

    Each path's name, size, type and times, as a block; ``-c`` formats
    them instead, with ``%n`` (name), ``%s`` (size in bytes), ``%F``
    (type), ``%y``/``%Y`` (modified, as a time or seconds since the
    epoch), ``%w``/``%W`` (created) and ``%%``.
    """
    fmt: str | None = None
    paths: list[str] = []
    args = iter(ctx.args)
    for arg in args:
        if arg in ("-c", "--format"):
            value = next(args, None)
            if value is None:
                raise TerminalError(f"stat: option {arg} requires a value")
            fmt = value
        elif arg.startswith("--format="):
            fmt = arg[len("--format=") :]
        elif arg.startswith("-c") and len(arg) > 2:
            fmt = arg[2:]
        elif arg == "--":
            paths.extend(args)
        elif arg.startswith("--"):
            raise TerminalError(f"stat: unknown option: {arg.split('=', 1)[0]}")
        elif arg.startswith("-") and arg != "-":
            raise TerminalError(f"stat: unknown option: {arg[:2]}")
        else:
            paths.append(arg)
    if not paths:
        raise TerminalError("stat: missing operand")
    errors: list[str] = []
    for path in paths:
        try:
            meta = ctx.fs.stat(path)
        except OSError as e:
            errors.append(f"stat: cannot stat '{path}': {fs_error_text(e)}")
            continue
        if fmt is not None:
            ctx.stdout.write(_stat_format(fmt, path, meta) + "\n")
            continue
        block = [
            f"  File: {path}",
            f"  Size: {meta.size}",
            f"  Type: {_file_type(meta)}",
            f"Modify: {_show_time(_when(meta.modified_at))}",
            f" Birth: {_show_time(_when(meta.created_at))}",
        ]
        ctx.stdout.write("\n".join(block) + "\n")
    return _failed(errors)


# =============================================================================
# realpath
# =============================================================================


def realpath(ctx: CommandContext) -> CommandResult | None:
    """``realpath [-e | -m] PATH...``

    Each path made absolute, with ``.``, ``..`` and repeated slashes
    resolved. By default its directory must exist; ``-e`` asks the path
    itself to, ``-m`` neither. There are no symlinks to follow.
    """
    flags, paths = _short_flags(
        "realpath",
        ctx.args,
        {
            "-e": "existing",
            "--canonicalize-existing": "existing",
            "-m": "missing",
            "--canonicalize-missing": "missing",
            "-s": "strip",
            "--strip": "strip",
            "--no-symlinks": "strip",
        },
    )
    if not paths:
        raise TerminalError("realpath: missing operand")
    errors: list[str] = []
    for path in paths:
        resolved = resolve_path(path, ctx.fs)
        # posixpath keeps a leading "//"; one root is enough
        resolved = "/" + resolved.lstrip("/")
        if "missing" not in flags:
            parent = posixpath.dirname(resolved) or "/"
            if "existing" in flags:
                ok = ctx.fs.exists(resolved)
            else:
                ok = ctx.fs.isdir(parent)
            if not ok:
                errors.append(f"realpath: {path}: No such file or directory")
                continue
        ctx.stdout.write(resolved + "\n")
    return _failed(errors)
