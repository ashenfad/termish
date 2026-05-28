"""Control builtins: ``true``, ``false``.

POSIX no-ops. Their primary purpose is enabling shell idioms — most
importantly ``cmd || true`` to swallow a non-zero exit so a script can
continue. Without these, transcripts that include the common
``cmd || true`` pattern fail with ``true: command not found``.

Function names are suffixed with ``_cmd`` because ``file`` and the
boolean keywords ``True`` / ``False`` would otherwise collide with
built-in names; we follow the same convention used by ``zip_cmd``.
"""

from termish.context import CommandContext, CommandResult


def true_cmd(_ctx: CommandContext) -> CommandResult | None:
    """``true`` — succeed with no output."""
    return None


def false_cmd(_ctx: CommandContext) -> CommandResult | None:
    """``false`` — fail with exit code 1, no output, no diagnostic.

    The interpreter turns this into ``TerminalError("false: exited with code 1")``,
    which is correctly rescued by ``||`` and correctly propagates otherwise.
    """
    return CommandResult(exit_code=1, stderr="")
