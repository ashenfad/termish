"""Error types and type aliases for termish."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .context import CommandContext, CommandResult


class TerminalError(Exception):
    """Raised when a terminal command execution fails.

    ``exit_code`` preserves the failing command's exit status (127 for
    command-not-found, a failing ``CommandResult``'s own code, 1 for
    everything else) so callers can surface shell-faithful codes.

    ``stderr`` is what a terminal would print for this failure: ``None``
    means the message itself is the diagnostic (the common case for
    builtin errors like ``cat: /missing: No such file or directory``),
    a non-empty string is the failing handler's own stderr verbatim,
    and ``""`` means nothing reached the screen: the command failed
    silently (e.g. ``false``, or ``grep`` with no match) or its stderr
    was redirected (``2>&1``, ``2>/dev/null``, ``2>file``).

    ``message`` always describes the failure, even a silent one
    (``grep: exited with code 1``). A handler's own stderr appears in it
    once, prefixed with the command's name only when it doesn't already
    start with it. To show a failure the way a terminal would, use
    ``display_stderr``.
    """

    def __init__(
        self,
        message: str,
        partial_output: str = "",
        exit_code: int = 1,
        stderr: str | None = None,
    ):
        self.message = message
        self.partial_output = partial_output
        self.exit_code = exit_code
        self.stderr = stderr
        super().__init__(message)

    @property
    def display_stderr(self) -> str:
        """The stderr a terminal shows for this failure.

        ``""`` when the command failed silently or its stderr was
        redirected; otherwise ``message``, the diagnostic as it reached
        the screen.
        """
        return "" if self.stderr == "" else self.message


# Command function signature:
# func(ctx: CommandContext) -> CommandResult | None
# Write output to ctx.stdout.  Raise TerminalError on failure.
# Return None for success, or a CommandResult for exit_code / stderr.
CommandFunc = Callable[["CommandContext"], "CommandResult | None"]
