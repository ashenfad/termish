"""
Meta commands that invoke other commands.
"""

import contextvars
from typing import TYPE_CHECKING

from termish.context import CommandContext, CommandResult
from termish.errors import TerminalError

from ._dispatch import dispatch_argv, stderr_of

_MAX_XARGS_DEPTH = 16
_xargs_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "xargs_depth", default=0
)

if TYPE_CHECKING:
    pass


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


def xargs(ctx: CommandContext) -> CommandResult | None:
    """Build and execute commands from standard input."""
    args, stdin, stdout, fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    depth = _xargs_depth.get()

    if depth >= _MAX_XARGS_DEPTH:
        raise TerminalError(
            f"xargs: maximum recursion depth exceeded ({_MAX_XARGS_DEPTH})"
        )

    replace, max_args, null, verbose, cmd_name, cmd_base_args = _parse_xargs_args(args)

    # Import the resolved command table here to avoid circular import.
    # _resolve_command returns the merged (injected-over-builtins) handler.
    from termish.interpreter.core import _resolve_command

    cmd_func = _resolve_command(cmd_name)
    if cmd_func is None:
        raise TerminalError(f"xargs: {cmd_name}: command not found")

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

    # What the commands say on stderr while succeeding becomes xargs's own
    # stderr: shown, as it is when the command runs directly, and never
    # fed down the pipe with the output.
    warnings: list[str] = []

    def execute_cmd(cmd_args: list[str]) -> None:
        """Execute command with given args and write output to stdout."""
        if verbose:
            stdout.write(f"{cmd_name} {' '.join(cmd_args)}\n")

        token = _xargs_depth.set(depth + 1)
        try:
            output, warning = dispatch_argv([cmd_name, *cmd_args], fs, ctx.env)
        finally:
            _xargs_depth.reset(token)
        if warning:
            warnings.append(warning.rstrip("\n"))
        # The sub-command's output is moved, not read: pass its bytes
        # through so a binary-producing command survives xargs.
        stdout.flush()
        stdout.buffer.write(output)

    try:
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
    except TerminalError as e:
        if not warnings:
            raise
        # the warnings of the runs before it are shown too
        raise TerminalError(
            e.message,
            exit_code=e.exit_code,
            stderr="\n".join(w for w in [*warnings, stderr_of(e)] if w),
        )
    if warnings:
        return CommandResult(stderr="\n".join(warnings))
    return None
