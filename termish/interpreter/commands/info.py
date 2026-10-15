"""What's here: ``which``, ``command``, ``help``.

They read the command table the script runs with, so a host's own
commands show beside the builtins. A host command describes itself
with a ``description`` string attribute on its handler, which ``help``
shows; one without is listed by name.

Mirrors termish-ts's ``info.ts``.
"""

from collections.abc import Mapping

from termish.context import CommandContext, CommandResult
from termish.errors import CommandFunc, TerminalError

from .help_text import HELP, HelpEntry


def _commands() -> tuple[Mapping[str, CommandFunc], Mapping[str, CommandFunc]]:
    """Every command (injected over built-in), and the built-ins."""
    # Deferred: core imports the command modules, this one included.
    from termish.interpreter.core import BUILTINS, _injected_commands

    merged = dict(BUILTINS)
    merged.update(_injected_commands.get())
    return merged, BUILTINS


def _is_builtin(name: str) -> bool:
    commands, builtins = _commands()
    return name in commands and commands[name] is builtins.get(name)


def _help_of(name: str) -> HelpEntry | None:
    """``name``'s help: the builtin's own, a host command's
    ``description``, or None for a host command without one."""
    commands, _ = _commands()
    if _is_builtin(name):
        return HELP.get(name)
    description = getattr(commands.get(name), "description", None)
    return HelpEntry(description, "") if isinstance(description, str) else None


def which(ctx: CommandContext) -> CommandResult | None:
    """``which NAME...`` — each command that exists, by name; exit 1 if
    any doesn't."""
    if not ctx.args:
        raise TerminalError("which: missing operand")
    commands, _ = _commands()
    missing: list[str] = []
    for name in ctx.args:
        if name in commands:
            ctx.stdout.write(f"{name}\n")
        else:
            missing.append(f"which: no {name}")
    return CommandResult(exit_code=1, stderr="\n".join(missing)) if missing else None


def command(ctx: CommandContext) -> CommandResult | None:
    """``command -v NAME...`` (each that exists, by name), ``command -V
    NAME...`` (what each is), or ``command NAME [ARG...]`` (run it)."""
    if not ctx.args:
        return None
    first, *rest = ctx.args
    commands, _ = _commands()
    if first in ("-v", "-V"):
        if not rest:
            raise TerminalError(f"command: {first}: missing operand")
        missing: list[str] = []
        for name in rest:
            if name not in commands:
                missing.append(f"command: {name}: not found")
            elif first == "-v":
                ctx.stdout.write(f"{name}\n")
            else:
                kind = "a builtin" if _is_builtin(name) else "a host command"
                ctx.stdout.write(f"{name} is {kind}\n")
        return (
            CommandResult(exit_code=1, stderr="\n".join(missing)) if missing else None
        )
    if first.startswith("-"):
        raise TerminalError(f"command: unknown option: {first}")
    func = commands.get(first)
    if func is None:
        raise TerminalError(f"{first}: command not found", exit_code=127)
    return func(
        CommandContext(
            args=rest, stdin=ctx.stdin, stdout=ctx.stdout, fs=ctx.fs, env=ctx.env
        )
    )


def help_cmd(ctx: CommandContext) -> CommandResult | None:
    """``help [NAME]`` — every command with a line on what it does, or how
    NAME is used."""
    if len(ctx.args) > 1:
        raise TerminalError("help: too many arguments")
    commands, _ = _commands()
    if ctx.args:
        name = ctx.args[0]
        if name not in commands:
            raise TerminalError(f"help: no such command: {name}")
        entry = _help_of(name)
        if entry is None:
            ctx.stdout.write(f"{name}: a command of this host, with no help here\n")
            return None
        ctx.stdout.write(f"{name} - {entry.summary}\n")
        if entry.usage:
            ctx.stdout.write(f"usage: {entry.usage}\n")
        return None
    names = sorted(commands)
    width = max(len(n) for n in names)
    lines = []
    for n in names:
        entry = _help_of(n)
        lines.append(n if entry is None else f"{n.ljust(width)}  {entry.summary}")
    ctx.stdout.write(
        "Commands (help NAME shows how one is used):\n" + "\n".join(lines) + "\n"
    )
    return None
