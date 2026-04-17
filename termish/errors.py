"""Error types and type aliases for termish."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .context import CommandContext, CommandResult


class TerminalError(Exception):
    """Raised when a terminal command execution fails."""

    def __init__(self, message: str, partial_output: str = ""):
        self.message = message
        self.partial_output = partial_output
        super().__init__(message)


# Command function signature:
# func(ctx: CommandContext) -> CommandResult | None
# Write output to ctx.stdout.  Raise TerminalError on failure.
# Return None for success, or a CommandResult for exit_code / stderr.
CommandFunc = Callable[["CommandContext"], "CommandResult | None"]
