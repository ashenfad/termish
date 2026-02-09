"""faketerm: Virtual terminal with shell-like commands over a pluggable filesystem."""

from .ast import Command, Pipeline, Redirect, Script
from .errors import TerminalError
from .fs import FileInfo, FileMetadata, FileSystem, MemoryFS
from .interpreter import execute_script
from .parser import ParseError, to_script

__all__ = [
    "Command",
    "FileInfo",
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


def execute(script_text: str, fs: FileSystem) -> str:
    """Parse and execute a shell script against a filesystem.

    Args:
        script_text: Shell command string (e.g. "ls -la | grep .py").
        fs: Filesystem to operate on.

    Returns:
        Captured stdout as a string.

    Raises:
        TerminalError: If a command fails.
        ParseError: If the script has invalid syntax.
    """
    return execute_script(to_script(script_text), fs)
