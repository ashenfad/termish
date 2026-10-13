"""Running a command from an argv, as ``xargs`` and ``find -exec`` do."""

from __future__ import annotations

from typing import NamedTuple

from termish.context import CommandContext, PipeStream
from termish.errors import TerminalError
from termish.fs import FileSystem


class Dispatched(NamedTuple):
    """What a command that succeeded produced."""

    #: Its stdout, byte for byte.
    output: bytes
    #: What it said on stderr while succeeding (a warning), or "".
    stderr: str


def dispatch_argv(argv: list[str], fs: FileSystem, env: dict[str, str]) -> Dispatched:
    """Run ``argv`` (``[name, *args]``) with empty stdin.

    The argv is dispatched as it is, never re-parsed by the shell, through
    the same table the interpreter uses, injected commands included.
    Raises ``TerminalError`` when the command is missing (127), fails, or
    raises.
    """
    # Deferred: core imports the command modules that import this one.
    from termish.interpreter.core import _resolve_command

    name, *args = argv
    func = _resolve_command(name)
    if func is None:
        raise TerminalError(f"{name}: command not found", exit_code=127)

    stdout = PipeStream()
    try:
        result = func(
            CommandContext(args=args, stdin=PipeStream(), stdout=stdout, fs=fs, env=env)
        )
    except TerminalError:
        raise
    except Exception as e:
        raise TerminalError(f"{name}: execution error: {e}")
    if result is not None and result.exit_code != 0:
        raise TerminalError(
            f"{name}: {result.stderr}"
            if result.stderr
            else f"{name}: exited with code {result.exit_code}",
            exit_code=result.exit_code,
            stderr=result.stderr,
        )
    return Dispatched(stdout.getvalue(), result.stderr if result is not None else "")


def stderr_of(e: TerminalError) -> str:
    """What a failure shows on stderr ("" if it failed silently)."""
    return e.message if e.stderr is None else e.stderr


def join_stderr(parts: list[str]) -> str:
    """Several commands' stderr as one, each as written: a newline is
    added only where one is missing, so blank lines survive."""
    return "".join(p if p.endswith("\n") else p + "\n" for p in parts if p)
