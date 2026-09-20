"""
I/O commands for the terminal interpreter.
"""

import re

from termish.context import CommandContext, CommandResult
from termish.errors import TerminalError

from ._argparse import CommandArgParser
from ._util import split_lines


def echo(ctx: CommandContext) -> CommandResult | None:
    """Echo arguments to stdout."""
    args, _stdin, stdout, _fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    # Manual flag parsing: echo treats unknown flags as literal text
    newline = True
    interpret_escapes = False
    text_start = 0
    for i, arg in enumerate(args):
        match arg:
            case "-n":
                newline = False
                text_start = i + 1
            case "-e":
                interpret_escapes = True
                text_start = i + 1
            case "-ne" | "-en":
                newline = False
                interpret_escapes = True
                text_start = i + 1
            case _:
                break

    text = " ".join(args[text_start:])

    if interpret_escapes:
        result: list[str] = []
        j = 0
        while j < len(text):
            if text[j] == "\\" and j + 1 < len(text):
                match text[j + 1]:
                    case "n":
                        result.append("\n")
                    case "t":
                        result.append("\t")
                    case "\\":
                        result.append("\\")
                    case "a":
                        result.append("\a")
                    case "b":
                        result.append("\b")
                    case other:
                        result.append("\\" + other)
                j += 2
            else:
                result.append(text[j])
                j += 1
        text = "".join(result)

    stdout.write(text + ("\n" if newline else ""))


# Backslash escapes interpreted inside printf's FORMAT operand.
_PRINTF_ESCAPES = {
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
    '"': '"',
    "'": "'",
}

# Conversions printf understands.  Field widths, precision, flags, float
# formats and %b are deliberately absent: anything else in a conversion
# is an error rather than silently wrong output.
_PRINTF_CONVERSIONS = "sdic"

# Characters a wider printf would accept between "%" and the conversion
# character (flags, width, precision).  Only used to quote the whole
# offending specifier back in the error message.
_PRINTF_SPEC_CHARS = "-+ #0123456789.'"

_PRINTF_DECIMAL = re.compile(r"[+-]?[0-9]+\Z")
_PRINTF_HEX = re.compile(r"[+-]?0[xX][0-9a-fA-F]+\Z")
_PRINTF_OCTAL = re.compile(r"[+-]?0[0-7]+\Z")
# A leading zero claims the operand for octal, so ``08`` is not decimal
# eight but an octal number with a digit octal does not have.
_PRINTF_OCTAL_SHAPED = re.compile(r"[+-]?0[0-9]+\Z")


def _printf_unescape(fmt: str) -> tuple[str, int]:
    """Expand one backslash escape at the start of ``fmt``.

    ``fmt`` begins at the backslash.  Returns the replacement text and how
    many characters were consumed.  ``\\0ooo`` and ``\\ooo`` take up to
    three octal digits; an escape with no meaning keeps its backslash and
    is emitted as written, which is what bash does.
    """
    if len(fmt) < 2:
        return "\\", 1

    ch = fmt[1]
    if ch in _PRINTF_ESCAPES:
        return _PRINTF_ESCAPES[ch], 2

    if ch in "01234567":
        digits = fmt[2:5] if ch == "0" else fmt[1:4]
        octal = ""
        for d in digits:
            if d not in "01234567":
                break
            octal += d
        if ch != "0":
            consumed = 1 + len(octal)
        else:
            consumed = 2 + len(octal)
            octal = octal or "0"
        return chr(int(octal, 8) & 0xFF), consumed

    return "\\" + ch, 2


def _printf_parse(fmt: str) -> list[tuple[str, str]]:
    """Split FORMAT into ``("lit", text)`` and ``("conv", char)`` items.

    Escapes are expanded here, once, because they never depend on the
    arguments.  Raises TerminalError naming any conversion that is not
    supported.
    """
    items: list[tuple[str, str]] = []
    literal: list[str] = []
    i = 0
    while i < len(fmt):
        ch = fmt[i]
        if ch == "\\":
            text, consumed = _printf_unescape(fmt[i:])
            literal.append(text)
            i += consumed
            continue
        if ch != "%":
            literal.append(ch)
            i += 1
            continue

        # A conversion: flush the literal run, then decide what follows "%".
        j = i + 1
        while j < len(fmt) and fmt[j] in _PRINTF_SPEC_CHARS:
            j += 1
        if j >= len(fmt):
            raise TerminalError(f"printf: {fmt[i:]}: missing conversion character")
        spec = fmt[i : j + 1]
        conv = fmt[j]
        if conv == "%" and j == i + 1:
            literal.append("%")
            i = j + 1
            continue
        if conv not in _PRINTF_CONVERSIONS or j != i + 1:
            raise TerminalError(f"printf: {spec}: unsupported conversion")
        if literal:
            items.append(("lit", "".join(literal)))
            literal = []
        items.append(("conv", conv))
        i = j + 1

    if literal:
        items.append(("lit", "".join(literal)))
    return items


def _printf_int(text: str) -> int:
    """Convert an argument for %d / %i.

    Decimal, ``0x``-prefixed hex and leading-zero octal are accepted, each
    with an optional sign.  Anything else raises ValueError, which the
    caller reports as an invalid number.  A leading zero commits the
    operand to octal: ``08`` is invalid, not eight, because a shell that
    read it as eight would print a number where bash prints an error.
    """
    stripped = text.strip()
    if _PRINTF_HEX.match(stripped):
        return int(stripped, 16)
    if _PRINTF_OCTAL.match(stripped):
        return int(stripped, 8)
    if _PRINTF_OCTAL_SHAPED.match(stripped):
        raise ValueError(text)
    if _PRINTF_DECIMAL.match(stripped):
        return int(stripped, 10)
    raise ValueError(text)


def printf(ctx: CommandContext) -> CommandResult | None:
    """Write formatted output — no trailing newline is ever added."""
    args, stdout = ctx.args, ctx.stdout
    # ``--`` ends the options, of which there are none, so it is dropped
    # rather than taken as the format: a script hardened against a format
    # that starts with ``-`` must not print a literal ``--``.
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        raise TerminalError("printf: usage: printf FORMAT [ARGUMENT]...")

    items = _printf_parse(args[0])
    operands = args[1:]
    conversions = sum(1 for kind, _ in items if kind == "conv")
    errors: list[str] = []

    # POSIX format reuse: the format is applied again until the arguments
    # run out.  A format with no conversions is written exactly once, no
    # matter how many arguments follow it.
    pos = 0
    while True:
        for kind, value in items:
            if kind == "lit":
                stdout.write(value)
                continue
            arg = operands[pos] if pos < len(operands) else None
            pos += 1
            match value:
                case "s":
                    stdout.write(arg if arg is not None else "")
                case "d" | "i":
                    if arg is None:
                        stdout.write("0")
                    else:
                        try:
                            stdout.write(str(_printf_int(arg)))
                        except ValueError:
                            errors.append(f"printf: {arg}: invalid number")
                            stdout.write("0")
                case "c":
                    stdout.write(arg[:1] if arg else "")
        if conversions == 0 or pos >= len(operands):
            break

    if errors:
        return CommandResult(exit_code=1, stderr="\n".join(errors))
    return None


def cat(ctx: CommandContext) -> CommandResult | None:
    """Concatenate files and print on the standard output."""
    args, stdin, stdout, fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    parser = CommandArgParser(prog="cat", add_help=False)
    parser.add_argument(
        "-A", "--show-all", action="store_true", help="equivalent to -eT"
    )
    parser.add_argument("-e", action="store_true", help="display $ at end of each line")
    parser.add_argument(
        "-T", "--show-tabs", action="store_true", help="display TAB as ^I"
    )
    parser.add_argument(
        "-n", "--number", action="store_true", help="number all output lines"
    )
    parser.add_argument("files", nargs="*")

    parsed, unknown = parser.parse_known_args(args)
    if unknown:
        raise TerminalError(f"cat: unknown option: {unknown[0]}")

    show_ends = parsed.e or parsed.show_all
    show_tabs = parsed.show_tabs or parsed.show_all
    show_numbers = parsed.number

    def format_content(content: str) -> str:
        lines = content.splitlines(keepends=True)
        result = []
        for i, line in enumerate(lines):
            # Handle line ending
            has_newline = line.endswith("\n")
            line_content = line.rstrip("\n")

            # Show tabs as ^I
            if show_tabs:
                line_content = line_content.replace("\t", "^I")

            # Show end of line marker
            if show_ends:
                line_content = line_content + "$"

            # Add line number
            if show_numbers:
                line_content = f"{i + 1:6d}  {line_content}"

            # Restore newline if original had one
            if has_newline:
                line_content += "\n"

            result.append(line_content)

        # Handle case where content doesn't end with newline
        if content and not content.endswith("\n") and show_ends:
            # Already handled above
            pass

        return "".join(result)

    display = show_ends or show_tabs or show_numbers

    def emit(data: bytes) -> None:
        """Copy content out: bytes unchanged, unless a display flag asks
        for a rendering, which is a text view by definition."""
        if display:
            stdout.write(format_content(data.decode("utf-8", errors="replace")))
        else:
            stdout.flush()
            stdout.buffer.write(data)

    if not parsed.files:
        emit(stdin.buffer.read())
        return

    for path in parsed.files:
        if path == "-":
            emit(stdin.buffer.read())
            continue

        try:
            emit(fs.read(path))
        except FileNotFoundError:
            raise TerminalError(f"cat: {path}: No such file or directory")
        except IsADirectoryError:
            raise TerminalError(f"cat: {path}: Is a directory")
        except Exception as e:
            raise TerminalError(f"cat: {path}: {e}")


def head(ctx: CommandContext) -> CommandResult | None:
    """Output the first part of files."""
    args, stdin, stdout, fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    # Pre-process: rewrite -N shorthand to -n N
    processed_args = list(args)
    if (
        processed_args
        and processed_args[0].startswith("-")
        and processed_args[0][1:].isdigit()
    ):
        processed_args = ["-n", processed_args[0][1:]] + processed_args[1:]

    parser = CommandArgParser(prog="head", add_help=False)
    parser.add_argument("-n", "--lines", type=int, default=10)
    parser.add_argument("-c", "--bytes", type=int, default=0)
    parser.add_argument("files", nargs="*")

    parsed, unknown = parser.parse_known_args(processed_args)
    if unknown:
        raise TerminalError(f"head: unknown option: {unknown[0]}")

    byte_mode = parsed.bytes > 0
    limit = parsed.bytes if byte_mode else parsed.lines

    def emit(data: bytes) -> None:
        """Write the leading bytes or lines, whichever was asked for."""
        stdout.flush()
        if byte_mode:
            stdout.buffer.write(data[:limit])
        else:
            for line in split_lines(data)[:limit]:
                stdout.buffer.write(line)

    if not parsed.files:
        emit(stdin.buffer.read())
        return

    for i, path in enumerate(parsed.files):
        if len(parsed.files) > 1:
            stdout.write(f"==> {path} <==\n")

        try:
            emit(fs.read(path))
        except Exception as e:
            raise TerminalError(f"head: cannot open '{path}': {e}")

        if i < len(parsed.files) - 1:
            stdout.write("\n")


def tail(ctx: CommandContext) -> CommandResult | None:
    """Output the last part of files."""
    args, stdin, stdout, fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    # Pre-process: rewrite -N shorthand to -n N
    processed_args = list(args)
    if (
        processed_args
        and processed_args[0].startswith("-")
        and processed_args[0][1:].isdigit()
    ):
        processed_args = ["-n", processed_args[0][1:]] + processed_args[1:]

    parser = CommandArgParser(prog="tail", add_help=False)
    parser.add_argument("-n", "--lines", type=str, default="10")
    parser.add_argument("-c", "--bytes", type=int, default=0)
    parser.add_argument("files", nargs="*")

    parsed, unknown = parser.parse_known_args(processed_args)
    if unknown:
        raise TerminalError(f"tail: unknown option: {unknown[0]}")

    byte_mode = parsed.bytes > 0

    if byte_mode:
        byte_limit = parsed.bytes

        def emit_bytes(data: bytes) -> None:
            stdout.flush()
            stdout.buffer.write(data[-byte_limit:])

        if not parsed.files:
            emit_bytes(stdin.buffer.read())
            return

        for i, path in enumerate(parsed.files):
            if len(parsed.files) > 1:
                stdout.write(f"==> {path} <==\n")
            try:
                emit_bytes(fs.read(path))
            except Exception as e:
                raise TerminalError(f"tail: cannot open '{path}': {e}")
            if i < len(parsed.files) - 1:
                stdout.write("\n")
        return

    # Parse limit: "+N" means from line N onwards, plain N means last N lines
    # Handle case where parser splits "+3" into "+" and "3"
    limit_str = parsed.lines
    if limit_str == "+" and parsed.files and parsed.files[0].isdigit():
        limit_str = "+" + parsed.files.pop(0)

    from_start = False
    if limit_str.startswith("+"):
        from_start = True
        limit = int(limit_str[1:])
    else:
        limit = int(limit_str)

    def emit_lines(data: bytes) -> None:
        all_lines = split_lines(data)
        selected = all_lines[limit - 1 :] if from_start else all_lines[-limit:]
        stdout.flush()
        for line in selected:
            stdout.buffer.write(line)

    if not parsed.files:
        emit_lines(stdin.buffer.read())
        return

    for i, path in enumerate(parsed.files):
        if len(parsed.files) > 1:
            stdout.write(f"==> {path} <==\n")

        try:
            emit_lines(fs.read(path))
        except Exception as e:
            raise TerminalError(f"tail: cannot open '{path}': {e}")

        if i < len(parsed.files) - 1:
            stdout.write("\n")


def tee(ctx: CommandContext) -> CommandResult | None:
    """Read from stdin and write to stdout and files."""
    args, stdin, stdout, fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    parser = CommandArgParser(prog="tee", add_help=False)
    parser.add_argument("-a", "--append", action="store_true")
    parser.add_argument("files", nargs="*")

    parsed, unknown = parser.parse_known_args(args)
    if unknown:
        raise TerminalError(f"tee: unknown option: {unknown[0]}")

    content = stdin.buffer.read()

    # Write to stdout
    stdout.flush()
    stdout.buffer.write(content)

    # Write to each file
    mode = "a" if parsed.append else "w"
    for path in parsed.files:
        try:
            fs.write(path, content, mode=mode)
        except Exception as e:
            raise TerminalError(f"tee: {path}: {e}")
