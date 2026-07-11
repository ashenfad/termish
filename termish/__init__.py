"""termish: Virtual terminal with shell-like commands over a pluggable filesystem."""

from collections.abc import Mapping

from .ast import Command, Operator, Pipeline, Redirect, Script
from .context import CommandContext, CommandResult
from .errors import CommandFunc, TerminalError
from .fs import FileInfo, FileMetadata, FileSystem, MemoryFS
from .interpreter import execute_script
from .parser import ParseError, to_script

__all__ = [
    "Command",
    "CommandContext",
    "CommandFunc",
    "CommandResult",
    "FileInfo",
    "Operator",
    "FileMetadata",
    "FileSystem",
    "MemoryFS",
    "ParseError",
    "Pipeline",
    "Redirect",
    "Script",
    "TerminalError",
    "execute",
    "execute_script",
    "to_script",
]


def execute(
    script_text: str,
    fs: FileSystem,
    commands: Mapping[str, CommandFunc] | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Parse and execute a shell script against a filesystem.

    Args:
        script_text: Shell command string (e.g. "ls -la | grep .py").
        fs: Filesystem to operate on.
        commands: Optional mapping of injected command handlers.
            Injected commands override built-ins when names collide.
        env: Optional environment variables for ``$VAR`` expansion.
            The dict is shared with command handlers via ``ctx.env``;
            mutations are visible to later commands and to the caller,
            so a long-lived dict carries state across execute() calls.

    Returns:
        The terminal transcript as a string: captured stdout, plus
        stderr diagnostics from failures that execution continued past
        (``cmd; ...``, ``cmd || rescue``) and from warnings — what a
        real terminal would show on screen.

    Raises:
        TerminalError: If the last executed pipeline fails.
        ParseError: If the script has invalid syntax.
    """
    return execute_script(to_script(script_text), fs, commands=commands, env=env)
