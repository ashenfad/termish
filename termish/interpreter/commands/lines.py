"""Line commands: ``seq``, ``paste``, ``comm``, ``rev``.

Mirrors termish-ts's ``lines.ts``. A file's lines are its text split at
``\\n``, a final newline ending the last line rather than starting an
empty one.
"""

import math
import re

from termish.context import CommandContext, CommandResult
from termish.errors import TerminalError

from ._fserr import fs_error_text

#: The most numbers one ``seq`` writes.
SEQ_MAX = 1_000_000


def _split_lines(text: str) -> list[str]:
    if text == "":
        return []
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def _read_text(ctx: CommandContext, prog: str, path: str) -> str:
    """A file's text, or stdin's for ``-``."""
    if path == "-":
        return ctx.stdin.read()
    try:
        return ctx.fs.read(path).decode("utf-8", errors="replace")
    except OSError as e:
        raise TerminalError(f"{prog}: {path}: {fs_error_text(e)}")


# =============================================================================
# seq
# =============================================================================

_NUMBER_RE = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")


def _parse_number(text: str) -> tuple[float, int]:
    """The number, and its digits after the decimal point as written."""
    m = _NUMBER_RE.fullmatch(text)
    if m is None:
        raise TerminalError(f"seq: invalid floating point argument: '{text}'")
    mantissa = m.group(1)
    exp = 0 if m.group(2) is None else int(m.group(2)[1:])
    dot = mantissa.find(".")
    frac = 0 if dot == -1 else len(mantissa) - dot - 1
    return float(text), max(0, frac - exp)


def _format_scaled(n: int, places: int) -> str:
    """``n`` (in units of 10^-``places``) as decimal text."""
    sign = "-" if n < 0 else ""
    digits = str(abs(n)).rjust(places + 1, "0")
    if places == 0:
        return sign + digits
    return f"{sign}{digits[:-places]}.{digits[-places:]}"


def _seq_args(args: list[str]) -> tuple[str, bool, list[str]]:
    """``-s``/``-w`` and the numbers, read by hand: ``-1`` is a number,
    not a flag."""
    sep = "\n"
    equal = False
    operands: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if _NUMBER_RE.fullmatch(arg):
            operands.append(arg)
        elif arg in ("-w", "--equal-width"):
            equal = True
        elif arg in ("-s", "--separator"):
            if i + 1 >= len(args):
                raise TerminalError(f"seq: option {arg} requires a value")
            i += 1
            sep = args[i]
        elif arg.startswith("--separator="):
            sep = arg[len("--separator=") :]
        elif arg.startswith("-s"):
            sep = arg[2:]
        elif arg == "--":
            operands.extend(args[i + 1 :])
            break
        elif arg.startswith("-"):
            raise TerminalError(f"seq: unknown option: {arg}")
        else:
            operands.append(arg)
        i += 1
    return sep, equal, operands


def seq(ctx: CommandContext) -> CommandResult | None:
    """``seq [-s SEP] [-w] [FIRST [INCR]] LAST``

    Numbers from FIRST (default 1) by INCR (default 1) up to LAST, one
    per line or joined by SEP. Shown with as many decimals as FIRST or
    INCR has; ``-w`` zero-pads them to one width. At most ``SEQ_MAX``.
    """
    sep, equal, operands = _seq_args(ctx.args)
    if not operands:
        raise TerminalError("seq: missing operand")
    if len(operands) > 3:
        raise TerminalError(f"seq: extra operand '{operands[3]}'")
    nums = [_parse_number(o) for o in operands]
    first = nums[0] if len(operands) > 1 else (1.0, 0)
    incr = nums[1] if len(operands) == 3 else (1.0, 0)
    last = nums[-1]
    if incr[0] == 0:
        raise TerminalError(f"seq: invalid Zero increment value: '{operands[1]}'")

    places = max(first[1], incr[1])
    scale = 10**places
    start = _round_half_away(first[0] * scale)
    step = _round_half_away(incr[0] * scale)
    end = last[0] * scale

    def within(n: int) -> bool:
        # A tolerance below one unit of the last decimal shown, so a LAST
        # written with fewer decimals than the arithmetic carries counts.
        return n <= end + 1e-9 if step > 0 else n >= end - 1e-9

    out: list[str] = []
    n = start
    while within(n):
        if len(out) >= SEQ_MAX:
            raise TerminalError(f"seq: too many numbers (the limit is {SEQ_MAX})")
        out.append(_format_scaled(n, places))
        n += step
    if not out:
        return None
    if equal:
        width = max(len(s) for s in out)
        out = [
            "-" + s[1:].rjust(width - 1, "0")
            if s.startswith("-")
            else s.rjust(width, "0")
            for s in out
        ]
    ctx.stdout.write(sep.join(out) + "\n")
    return None


def _round_half_away(x: float) -> int:
    """Round as JavaScript's ``Math.round`` does (half up), not to even."""
    return math.floor(x + 0.5)


# =============================================================================
# paste
# =============================================================================


def _delimiters(spec: str) -> list[str]:
    """``-d``'s list, with ``\\n``, ``\\t``, ``\\\\`` and ``\\0`` (no
    delimiter) read."""
    out: list[str] = []
    i = 0
    while i < len(spec):
        ch = spec[i]
        if ch == "\\" and i + 1 < len(spec):
            i += 1
            nxt = spec[i]
            out.append({"n": "\n", "t": "\t", "0": ""}.get(nxt, nxt))
        else:
            out.append(ch)
        i += 1
    if not out:
        raise TerminalError("paste: delimiter list can't be empty")
    return out


def _paste_args(args: list[str]) -> tuple[bool, str | None, list[str]]:
    serial = False
    delims: str | None = None
    files: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg == "--":
            files.extend(args[i:])
            break
        if arg == "--serial":
            serial = True
        elif arg == "--delimiters" or arg.startswith("--delimiters="):
            if "=" in arg:
                delims = arg.split("=", 1)[1]
            elif i < len(args):
                delims = args[i]
                i += 1
            else:
                raise TerminalError(f"paste: option {arg} requires a value")
        elif arg.startswith("--"):
            raise TerminalError(f"paste: unknown option: {arg.split('=', 1)[0]}")
        elif arg.startswith("-") and arg != "-":
            # a cluster: -s, -d LIST, -sd LIST, -d, (the value attached)
            for k, ch in enumerate(arg[1:], start=1):
                if ch == "s":
                    serial = True
                elif ch == "d":
                    rest = arg[k + 1 :]
                    if rest:
                        delims = rest
                    elif i < len(args):
                        delims = args[i]
                        i += 1
                    else:
                        raise TerminalError("paste: option -d requires a value")
                    break
                else:
                    raise TerminalError(f"paste: unknown option: -{ch}")
        else:
            files.append(arg)
    return serial, delims, files


def paste(ctx: CommandContext) -> CommandResult | None:
    """``paste [-s] [-d LIST] [FILE...]``

    Each output line joins the files' lines at that position, by a tab
    or LIST's delimiters in turn; ``-s`` joins each file's lines into one
    line instead. ``-`` (or no file) is stdin, read a line at a time by
    each ``-`` in turn.
    """
    serial, spec, files = _paste_args(ctx.args)
    delims = _delimiters(spec) if spec is not None else ["\t"]
    if not files:
        files = ["-"]
    stdin_lines = _split_lines(ctx.stdin.read()) if "-" in files else []

    def join(cells: list[str]) -> str:
        out = ""
        for i, cell in enumerate(cells):
            out = cell if i == 0 else out + delims[(i - 1) % len(delims)] + cell
        return out

    if serial:
        stdin_taken = False
        for f in files:
            if f == "-":
                lines = [] if stdin_taken else stdin_lines
                stdin_taken = True
            else:
                lines = _split_lines(_read_text(ctx, "paste", f))
            ctx.stdout.write(join(lines) + "\n")
        return None

    columns: list[list[str] | None] = [
        None if f == "-" else _split_lines(_read_text(ctx, "paste", f)) for f in files
    ]
    stdin_cols = sum(1 for c in columns if c is None)
    rows = max((len(c) for c in columns if c is not None), default=0)
    if stdin_cols:
        rows = max(rows, -(-len(stdin_lines) // stdin_cols))
    nxt = 0
    for r in range(rows):
        cells: list[str] = []
        for c in columns:
            if c is None:
                cells.append(stdin_lines[nxt] if nxt < len(stdin_lines) else "")
                nxt += 1
            else:
                cells.append(c[r] if r < len(c) else "")
        ctx.stdout.write(join(cells) + "\n")
    return None


# =============================================================================
# comm
# =============================================================================


def comm(ctx: CommandContext) -> CommandResult | None:
    """``comm [-1] [-2] [-3] FILE1 FILE2``

    Compares two sorted files: lines only in FILE1, lines only in FILE2,
    and lines in both, in three tab-indented columns. ``-1``, ``-2`` and
    ``-3`` leave a column out. Either file may be ``-``, for stdin.
    """
    hide: set[str] = set()
    operands: list[str] = []
    for arg in ctx.args:
        if arg.startswith("-") and arg != "-" and len(arg) > 1:
            for ch in arg[1:]:
                if ch not in "123":
                    raise TerminalError(f"comm: unknown option: -{ch}")
                hide.add(ch)
        else:
            operands.append(arg)
    if len(operands) < 2:
        raise TerminalError("comm: missing operand")
    if len(operands) > 2:
        raise TerminalError("comm: too many arguments")
    p1, p2 = operands
    if p1 == "-" and p2 == "-":
        raise TerminalError("comm: only one file may be stdin")
    a = _split_lines(_read_text(ctx, "comm", p1))
    b = _split_lines(_read_text(ctx, "comm", p2))
    show1, show2, show3 = "1" not in hide, "2" not in hide, "3" not in hide
    pre2 = "\t" if show1 else ""
    pre3 = pre2 + ("\t" if show2 else "")
    i = j = 0
    out: list[str] = []
    while i < len(a) or j < len(b):
        x = a[i] if i < len(a) else None
        y = b[j] if j < len(b) else None
        if y is None or (x is not None and x < y):
            if show1:
                out.append(f"{x}\n")
            i += 1
        elif x is None or y < x:
            if show2:
                out.append(f"{pre2}{y}\n")
            j += 1
        else:
            if show3:
                out.append(f"{pre3}{x}\n")
            i += 1
            j += 1
    ctx.stdout.write("".join(out))
    return None


# =============================================================================
# rev
# =============================================================================


def rev(ctx: CommandContext) -> CommandResult | None:
    """``rev [FILE...]`` — each line's characters in reverse order."""
    for f in ctx.args or ["-"]:
        text = _read_text(ctx, "rev", f)
        ctx.stdout.write("\n".join(line[::-1] for line in text.split("\n")))
    return None
