"""
Meta commands that invoke other commands.
"""

import io
from typing import TYPE_CHECKING, TextIO

from termish.errors import TerminalError
from termish.fs import FileSystem

if TYPE_CHECKING:
    from termish.errors import CommandFunc


def _parse_xargs_args(
    args: list[str],
) -> tuple[str | None, int | None, bool, bool, str, list[str]]:
    """Parse xargs options manually, stopping at the first non-option token.

    Returns (replace, max_args, null, verbose, cmd_name, cmd_base_args).
    """
    replace: str | None = None
    max_args: int | None = None
    null = False
    verbose = False

    i = 0
    while i < len(args):
        arg = args[i]
        match arg:
            case "-I" | "--replace":
                i += 1
                if i >= len(args):
                    raise TerminalError("xargs: option -I requires an argument")
                replace = args[i]
            case s if s.startswith("-I") and len(s) > 2:
                replace = s[2:]
            case "-n" | "--max-args":
                i += 1
                if i >= len(args):
                    raise TerminalError("xargs: option -n requires an argument")
                try:
                    max_args = int(args[i])
                except ValueError:
                    raise TerminalError(f"xargs: invalid number: {args[i]}")
            case s if s.startswith("-n") and len(s) > 2 and s[2:].isdigit():
                max_args = int(s[2:])
            case "-0" | "--null":
                null = True
            case "-t" | "--verbose":
                verbose = True
            case "-r" | "--no-run-if-empty":
                pass  # Default behavior already matches -r semantics
            case s if s.startswith("-"):
                raise TerminalError(f"xargs: unknown option: {s}")
            case _:
                # First non-option token is the command name; rest are its args
                return replace, max_args, null, verbose, arg, args[i + 1 :]
        i += 1

    # No command specified — default to echo
    return replace, max_args, null, verbose, "echo", []


def xargs(args: list[str], stdin: TextIO, stdout: TextIO, fs: FileSystem) -> None:
    """Build and execute commands from standard input."""
    replace, max_args, null, verbose, cmd_name, cmd_base_args = _parse_xargs_args(
        args
    )

    # Import BUILTINS here to avoid circular import
    from termish.interpreter.core import BUILTINS

    if cmd_name not in BUILTINS:
        raise TerminalError(f"xargs: {cmd_name}: command not found")

    cmd_func: "CommandFunc" = BUILTINS[cmd_name]

    # Read and split input
    input_text = stdin.read()
    if not input_text.strip():
        return  # No input, no commands

    if null:
        # Null-delimited
        items = [item for item in input_text.split("\0") if item]
    else:
        # Whitespace/newline delimited
        items = input_text.split()

    if not items:
        return

    def execute_cmd(cmd_args: list[str]) -> None:
        """Execute command with given args and write output to stdout."""
        if verbose:
            stdout.write(f"{cmd_name} {' '.join(cmd_args)}\n")

        cmd_stdin = io.StringIO()
        cmd_stdout = io.StringIO()
        cmd_func(cmd_args, cmd_stdin, cmd_stdout, fs)
        stdout.write(cmd_stdout.getvalue())

    if replace:
        # -I mode: run command once per item, substituting placeholder
        for item in items:
            cmd_args = [arg.replace(replace, item) for arg in cmd_base_args]
            execute_cmd(cmd_args)
    elif max_args:
        # -n mode: batch items
        for i in range(0, len(items), max_args):
            batch = items[i : i + max_args]
            execute_cmd(cmd_base_args + batch)
    else:
        # Default: all items as arguments to single command
        execute_cmd(cmd_base_args + items)
