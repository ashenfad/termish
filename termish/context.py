"""Command context and result types for termish command handlers."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TextIO, cast

if TYPE_CHECKING:
    from termish.fs.protocol import FileSystem


class PipeStream(io.TextIOWrapper):
    """A text view over the bytes a pipeline stage carries.

    Pipes and redirects move bytes; handlers mostly want text, so this
    is a UTF-8 ``TextIOWrapper`` over an in-memory byte buffer, with
    undecodable input replaced by U+FFFD rather than raised on, and
    newlines never translated (``\\r\\n`` in, ``\\r\\n`` out). A line
    ends at ``\\n`` and nowhere else, which is what a ``StringIO`` gave
    handlers that read line by line: a bare ``\\r`` stays inside its
    line rather than ending one. Content that must survive byte for
    byte goes through ``.buffer``.

    Text writes are passed straight to the byte buffer, so text and
    bytes land in the order they were written.
    """

    def __init__(self, initial: bytes = b"") -> None:
        super().__init__(
            io.BytesIO(initial),
            encoding="utf-8",
            errors="replace",
            newline="\n",
            write_through=True,
        )

    def getvalue(self) -> bytes:
        """Everything written to this stream so far, as bytes."""
        self.flush()
        return cast(io.BytesIO, self.buffer).getvalue()


@dataclass
class CommandContext:
    """Everything a command handler needs to run.

    Both built-in and injected commands receive this as their sole argument.
    Fields may be added in future versions with defaults, so handlers should
    tolerate extra attributes they don't use.

    ``stdin`` and ``stdout`` are text streams, and ``.buffer`` on either
    is the one binary path: ``stdout.buffer.write(data)`` emits bytes
    unchanged, ``stdin.buffer.read()`` consumes them unchanged.  A
    handler that mixes the two views of one stream must flush between
    them — call ``stdout.flush()`` before switching to
    ``stdout.buffer.write()``, and never read ``stdin`` as text before
    reading ``stdin.buffer``, because the text side reads ahead and the
    bytes it consumed are gone.  A handler that only moves content —
    copying stdin to stdout, writing a file out — should stay on
    ``.buffer`` and never decode: decoding and re-encoding corrupts
    anything that is not valid UTF-8.
    """

    args: list[str]
    """Parsed arguments (NOT including the command name)."""

    stdin: TextIO
    """Standard input — piped content from the previous pipeline stage,
    or an empty stream if this is the first command.  Reading it as text
    decodes UTF-8 and replaces undecodable bytes with U+FFFD, so binary
    input never raises; read ``stdin.buffer`` instead to get the bytes."""

    stdout: TextIO
    """Standard output — write results here.  The pipeline will capture
    what's written and pipe it to the next stage or to the final output.
    Text written here is encoded UTF-8; write ``stdout.buffer`` instead
    for content that must reach the next stage byte for byte."""

    fs: "FileSystem"
    """The filesystem to operate on."""

    env: dict[str, str] = field(default_factory=dict)
    """Environment variables backing ``$VAR`` expansion.  Shared across
    all commands in a script (and with the caller's dict passed via
    ``execute(env=...)``), so mutations are visible to later commands and
    to the caller."""


@dataclass
class CommandResult:
    """Optional return value from a command handler.

    Commands that return ``None`` are treated as successful (exit code 0,
    no stderr).  Return a ``CommandResult`` when you need to signal a
    non-zero exit code or emit stderr content.
    """

    exit_code: int = 0
    stderr: str = ""
