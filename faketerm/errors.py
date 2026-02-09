"""Error types and type aliases for faketerm."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, TextIO

if TYPE_CHECKING:
    from .fs.protocol import FileSystem


class TerminalError(Exception):
    """Raised when a terminal command execution fails."""

    def __init__(self, message: str, partial_output: str = ""):
        self.message = message
        self.partial_output = partial_output
        super().__init__(message)


# Command function signature:
# func(args: list[str], stdin: TextIO, stdout: TextIO, fs: FileSystem) -> None
# Raises TerminalError on failure.
CommandFunc = Callable[[list[str], TextIO, TextIO, "FileSystem"], None]
