"""
I/O commands for the terminal interpreter.
"""

from typing import TextIO

from termish.errors import TerminalError
from termish.fs import FileSystem

from ._argparse import CommandArgParser


def echo(args: list[str], stdin: TextIO, stdout: TextIO, fs: FileSystem) -> None:
    """Echo arguments to stdout."""
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


def cat(args: list[str], stdin: TextIO, stdout: TextIO, fs: FileSystem) -> None:
    """Concatenate files and print on the standard output."""
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

    if not parsed.files:
        content = stdin.read()
        stdout.write(
            format_content(content)
            if (show_ends or show_tabs or show_numbers)
            else content
        )
        return

    for path in parsed.files:
        if path == "-":
            content = stdin.read()
            stdout.write(
                format_content(content)
                if (show_ends or show_tabs or show_numbers)
                else content
            )
            continue

        try:
            content_bytes = fs.read(path)
            content = content_bytes.decode("utf-8", errors="replace")
            stdout.write(
                format_content(content)
                if (show_ends or show_tabs or show_numbers)
                else content
            )
        except FileNotFoundError:
            raise TerminalError(f"cat: {path}: No such file or directory")
        except IsADirectoryError:
            raise TerminalError(f"cat: {path}: Is a directory")
        except Exception as e:
            raise TerminalError(f"cat: {path}: {e}")


def head(args: list[str], stdin: TextIO, stdout: TextIO, fs: FileSystem) -> None:
    """Output the first part of files."""
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
    parser.add_argument("files", nargs="*")

    parsed, unknown = parser.parse_known_args(processed_args)
    if unknown:
        raise TerminalError(f"head: unknown option: {unknown[0]}")

    limit = parsed.lines

    if not parsed.files:
        count = 0
        for line in stdin:
            if count >= limit:
                break
            stdout.write(line)
            count += 1
        return

    for i, path in enumerate(parsed.files):
        if len(parsed.files) > 1:
            stdout.write(f"==> {path} <==\n")

        try:
            content_bytes = fs.read(path)
            content = content_bytes.decode("utf-8", errors="replace")
            lines = content.splitlines(keepends=True)
            for line in lines[:limit]:
                stdout.write(line)

        except Exception as e:
            raise TerminalError(f"head: cannot open '{path}': {e}")

        if i < len(parsed.files) - 1:
            stdout.write("\n")


def tail(args: list[str], stdin: TextIO, stdout: TextIO, fs: FileSystem) -> None:
    """Output the last part of files."""
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
    parser.add_argument("files", nargs="*")

    parsed, unknown = parser.parse_known_args(processed_args)
    if unknown:
        raise TerminalError(f"tail: unknown option: {unknown[0]}")

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

    def select_lines(all_lines: list[str]) -> list[str]:
        if from_start:
            return all_lines[limit - 1 :]
        return all_lines[-limit:]

    if not parsed.files:
        lines = stdin.readlines()
        for line in select_lines(lines):
            stdout.write(line)
        return

    for i, path in enumerate(parsed.files):
        if len(parsed.files) > 1:
            stdout.write(f"==> {path} <==\n")

        try:
            content_bytes = fs.read(path)
            content = content_bytes.decode("utf-8", errors="replace")
            lines = content.splitlines(keepends=True)
            for line in select_lines(lines):
                stdout.write(line)

        except Exception as e:
            raise TerminalError(f"tail: cannot open '{path}': {e}")

        if i < len(parsed.files) - 1:
            stdout.write("\n")


def tee(args: list[str], stdin: TextIO, stdout: TextIO, fs: FileSystem) -> None:
    """Read from stdin and write to stdout and files."""
    parser = CommandArgParser(prog="tee", add_help=False)
    parser.add_argument("-a", "--append", action="store_true")
    parser.add_argument("files", nargs="*")

    parsed, unknown = parser.parse_known_args(args)
    if unknown:
        raise TerminalError(f"tee: unknown option: {unknown[0]}")

    content = stdin.read()

    # Write to stdout
    stdout.write(content)

    # Write to each file
    mode = "a" if parsed.append else "w"
    for path in parsed.files:
        try:
            fs.write(path, content.encode("utf-8"), mode=mode)
        except Exception as e:
            raise TerminalError(f"tee: {path}: {e}")
