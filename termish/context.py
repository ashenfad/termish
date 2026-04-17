"""Command context and result types for termish command handlers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from termish.fs.protocol import FileSystem


@dataclass
class CommandContext:
    """Everything a command handler needs to run.

    Both built-in and injected commands receive this as their sole argument.
    Fields may be added in future versions with defaults, so handlers should
    tolerate extra attributes they don't use.
    """

    args: list[str]
    """Parsed arguments (NOT including the command name)."""

    stdin: TextIO
    """Standard input — piped content from the previous pipeline stage,
    or an empty StringIO if this is the first command."""

    stdout: TextIO
    """Standard output — write results here.  The pipeline will capture
    what's written and pipe it to the next stage or to the final output."""

    fs: "FileSystem"
    """The filesystem to operate on."""

    env: dict[str, str] = field(default_factory=dict)
    """Environment variables (reserved for future use)."""


@dataclass
class CommandResult:
    """Optional return value from a command handler.

    Commands that return ``None`` are treated as successful (exit code 0,
    no stderr).  Return a ``CommandResult`` when you need to signal a
    non-zero exit code or emit stderr content.
    """

    exit_code: int = 0
    stderr: str = ""
