"""
Stream editor for filtering and transforming text.
"""

import re
from dataclasses import dataclass, field
from typing import TextIO

from termish.errors import TerminalError
from termish.fs import FileSystem

from ._argparse import CommandArgParser

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Address:
    """A line address: line number, '$' for last line, or /regex/."""

    line: int | None = None
    last: bool = False
    regex: re.Pattern[str] | None = None


@dataclass(frozen=True)
class _AddressRange:
    """Zero, one, or two addresses bounding a command."""

    addr1: _Address | None = None
    addr2: _Address | None = None


@dataclass(frozen=True)
class _SedCommand:
    """A single sed command with its address range."""

    address: _AddressRange = field(default_factory=_AddressRange)
    command: str = ""  # 's', 'p', 'd'
    # For substitution:
    pattern: re.Pattern[str] | None = None
    replacement: str = ""
    sub_flags: str = ""


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _scan_delimited(text: str, pos: int, delim: str) -> tuple[str, int]:
    """Scan from *pos* until an unescaped *delim*. Returns (content, new_pos)."""
    result: list[str] = []
    while pos < len(text):
        ch = text[pos]
        if ch == "\\" and pos + 1 < len(text):
            next_ch = text[pos + 1]
            if next_ch == delim:
                result.append(delim)
                pos += 2
            else:
                result.append(ch + next_ch)
                pos += 2
        elif ch == delim:
            return ("".join(result), pos + 1)
        else:
            result.append(ch)
            pos += 1
    raise TerminalError("sed: unterminated 's' command")


def _translate_replacement(repl: str) -> str:
    """Translate sed replacement syntax to Python ``re.sub`` syntax."""
    result: list[str] = []
    i = 0
    while i < len(repl):
        if repl[i] == "\\" and i + 1 < len(repl):
            next_ch = repl[i + 1]
            if next_ch == "&":
                result.append("&")
            elif next_ch == "n":
                result.append("\n")
            elif next_ch == "t":
                result.append("\t")
            elif next_ch == "\\":
                result.append("\\\\")
            elif next_ch.isdigit():
                result.append("\\" + next_ch)
            else:
                result.append("\\" + next_ch)
            i += 2
        elif repl[i] == "&":
            result.append(r"\g<0>")
            i += 1
        else:
            result.append(repl[i])
            i += 1
    return "".join(result)


def _parse_address(text: str, pos: int) -> tuple[_Address | None, int]:
    """Parse a single address at *pos*. Returns (address | None, new_pos)."""
    if pos >= len(text):
        return None, pos

    ch = text[pos]

    # Line number
    if ch.isdigit():
        end = pos
        while end < len(text) and text[end].isdigit():
            end += 1
        return _Address(line=int(text[pos:end])), end

    # Last line
    if ch == "$":
        return _Address(last=True), pos + 1

    # Regex address /pattern/
    if ch == "/":
        content, new_pos = _scan_delimited(text, pos + 1, "/")
        try:
            compiled = re.compile(content)
        except re.error as e:
            raise TerminalError(f"sed: invalid regex in address: {e}")
        return _Address(regex=compiled), new_pos

    return None, pos


def _parse_substitution(text: str, pos: int) -> tuple[re.Pattern[str], str, str, int]:
    """Parse ``s/pattern/replacement/flags`` starting after the ``s``.

    Returns (compiled_pattern, translated_replacement, flags, new_pos).
    """
    if pos >= len(text):
        raise TerminalError("sed: unterminated 's' command")

    delim = text[pos]
    if delim.isalnum() or delim == "\\" or delim == "\n":
        raise TerminalError(f"sed: invalid delimiter '{delim}'")
    pos += 1

    raw_pattern, pos = _scan_delimited(text, pos, delim)
    raw_replacement, pos = _scan_delimited(text, pos, delim)

    # Read flags
    flags_str = ""
    while pos < len(text) and text[pos] in "gip":
        flags_str += text[pos]
        pos += 1

    if not raw_pattern:
        raise TerminalError("sed: empty regex in substitution")

    re_flags = 0
    if "i" in flags_str:
        re_flags |= re.IGNORECASE

    try:
        compiled = re.compile(raw_pattern, re_flags)
    except re.error as e:
        raise TerminalError(f"sed: invalid regex: {e}")

    replacement = _translate_replacement(raw_replacement)
    return compiled, replacement, flags_str, pos


def _parse_single_command(text: str) -> _SedCommand:
    """Parse a single sed command string (e.g. ``3,5s/a/b/g`` or ``$d``)."""
    text = text.strip()
    if not text:
        raise TerminalError("sed: empty command")

    pos = 0

    # Parse first address
    addr1, pos = _parse_address(text, pos)

    # Check for comma → second address
    addr2 = None
    if addr1 is not None and pos < len(text) and text[pos] == ",":
        pos += 1
        addr2, pos = _parse_address(text, pos)
        if addr2 is None:
            raise TerminalError("sed: invalid address range")

    addr_range = _AddressRange(addr1=addr1, addr2=addr2)

    # Parse command character
    if pos >= len(text):
        raise TerminalError("sed: missing command")

    cmd_char = text[pos]
    pos += 1

    if cmd_char == "s":
        pattern, replacement, sub_flags, pos = _parse_substitution(text, pos)
        cmd = _SedCommand(
            address=addr_range,
            command="s",
            pattern=pattern,
            replacement=replacement,
            sub_flags=sub_flags,
        )
    elif cmd_char in ("p", "d"):
        cmd = _SedCommand(address=addr_range, command=cmd_char)
    else:
        raise TerminalError(f"sed: unknown command: '{cmd_char}'")

    trailing = text[pos:].strip()
    if trailing:
        raise TerminalError(f"sed: trailing characters: '{trailing}'")

    return cmd


def _split_script(script: str) -> list[str]:
    """Split a sed script on ``;`` and newlines, respecting delimiters."""
    parts: list[str] = []
    current: list[str] = []
    i = 0
    # For s commands we need to skip past all 3 delimiters (s/pat/repl/flags)
    delim_char = ""
    delim_remaining = 0  # number of closing delimiters still expected

    while i < len(script):
        ch = script[i]

        if delim_remaining > 0:
            current.append(ch)
            if ch == "\\" and i + 1 < len(script):
                current.append(script[i + 1])
                i += 2
                continue
            if ch == delim_char:
                delim_remaining -= 1
            i += 1
            continue

        if ch == "s":
            current.append(ch)
            i += 1
            # Next char is the delimiter
            if i < len(script) and not script[i].isalnum():
                delim_char = script[i]
                delim_remaining = 2  # expect 2 more closing delimiters
                current.append(script[i])
                i += 1
            continue

        if ch in (";", "\n"):
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            i += 1
            continue

        current.append(ch)
        i += 1

    part = "".join(current).strip()
    if part:
        parts.append(part)

    return parts


def _parse_sed_script(script: str) -> list[_SedCommand]:
    """Parse a full sed script into a list of commands."""
    parts = _split_script(script)
    return [_parse_single_command(p) for p in parts]


# ---------------------------------------------------------------------------
# Processing engine
# ---------------------------------------------------------------------------


def _single_addr_matches(
    addr: _Address, line_num: int, total_lines: int, line_content: str
) -> bool:
    if addr.last:
        return line_num == total_lines
    if addr.line is not None:
        return line_num == addr.line
    if addr.regex is not None:
        return bool(addr.regex.search(line_content))
    return False


def _check_address(
    addr_range: _AddressRange,
    line_num: int,
    total_lines: int,
    line_content: str,
    range_active: list[bool],
    idx: int,
) -> bool:
    if addr_range.addr1 is None:
        return True

    if addr_range.addr2 is None:
        return _single_addr_matches(
            addr_range.addr1, line_num, total_lines, line_content
        )

    # Range address
    if range_active[idx]:
        if _single_addr_matches(addr_range.addr2, line_num, total_lines, line_content):
            range_active[idx] = False
        return True
    else:
        if _single_addr_matches(addr_range.addr1, line_num, total_lines, line_content):
            range_active[idx] = True
            if _single_addr_matches(
                addr_range.addr2, line_num, total_lines, line_content
            ):
                range_active[idx] = False
            return True
        return False


def _process_content(content: str, commands: list[_SedCommand], suppress: bool) -> str:
    """Apply sed commands to *content*. Returns processed text."""
    if not content:
        return ""

    lines = content.splitlines(keepends=True)

    # Track whether original content ended with a newline
    had_trailing_newline = content.endswith("\n")

    # Normalize: ensure every line ends with \n for consistent processing
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"

    total_lines = len(lines)
    range_active: list[bool] = [False] * len(commands)
    output_lines: list[str] = []

    for line_num, line in enumerate(lines, start=1):
        line_content = line.rstrip("\n")
        should_print = not suppress
        deleted = False

        for cmd_idx, cmd in enumerate(commands):
            if deleted:
                break

            if not _check_address(
                cmd.address,
                line_num,
                total_lines,
                line_content,
                range_active,
                cmd_idx,
            ):
                continue

            if cmd.command == "s":
                count = 0 if "g" in cmd.sub_flags else 1
                assert cmd.pattern is not None
                new_content, num_subs = cmd.pattern.subn(
                    cmd.replacement, line_content, count=count
                )
                line_content = new_content
                if num_subs > 0 and "p" in cmd.sub_flags:
                    output_lines.append(line_content + "\n")
            elif cmd.command == "p":
                output_lines.append(line_content + "\n")
            elif cmd.command == "d":
                deleted = True
                should_print = False

        if should_print and not deleted:
            output_lines.append(line_content + "\n")

    result = "".join(output_lines)

    # Preserve original trailing-newline behavior
    if not had_trailing_newline and result.endswith("\n"):
        result = result[:-1]

    return result


# ---------------------------------------------------------------------------
# Public command
# ---------------------------------------------------------------------------


def sed(args: list[str], stdin: TextIO, stdout: TextIO, fs: FileSystem) -> None:
    """Stream editor for filtering and transforming text."""
    parser = CommandArgParser(prog="sed", add_help=False)
    parser.add_argument("-n", "--quiet", "--silent", action="store_true")
    parser.add_argument("-i", "--in-place", action="store_true")
    parser.add_argument("-e", "--expression", action="append", dest="expressions")
    parser.add_argument("args_remainder", nargs="*")

    parsed, unknown = parser.parse_known_args(args)
    if unknown:
        raise TerminalError(f"sed: unknown option: {unknown[0]}")

    # Collect expressions and file list
    expressions: list[str] = []
    files: list[str] = []

    if parsed.expressions:
        expressions = parsed.expressions
        files = parsed.args_remainder
    else:
        if not parsed.args_remainder:
            raise TerminalError("sed: no expression given")
        expressions = [parsed.args_remainder[0]]
        files = parsed.args_remainder[1:]

    # Parse all expressions
    commands: list[_SedCommand] = []
    for expr in expressions:
        commands.extend(_parse_sed_script(expr))

    if not commands:
        raise TerminalError("sed: no expression given")

    # Validate -i usage
    if parsed.in_place and not files:
        raise TerminalError("sed: -i requires at least one file argument")

    # Process
    if not files:
        content = stdin.read()
        result = _process_content(content, commands, parsed.quiet)
        stdout.write(result)
    else:
        for path in files:
            try:
                content_bytes = fs.read(path)
                content = content_bytes.decode("utf-8", errors="replace")
            except FileNotFoundError:
                raise TerminalError(f"sed: {path}: No such file or directory")
            except IsADirectoryError:
                raise TerminalError(f"sed: {path}: Is a directory")

            result = _process_content(content, commands, parsed.quiet)

            if parsed.in_place:
                fs.write(path, result.encode("utf-8"), mode="w")
            else:
                stdout.write(result)
