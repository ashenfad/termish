import re
import shlex

from .ast import Command, Operator, Pipeline, Redirect, Script
from .quote_masker import mask_quotes, unmask_quotes


class ParseError(Exception):
    """Raised when the parser encounters invalid syntax."""

    pass


def _handle_line_continuation(text: str) -> str:
    """Remove backslash-newline sequences (line continuation).

    In shell, a backslash followed by a newline joins lines together.
    We also strip leading whitespace from the continuation line to match
    common usage patterns like:

        git add \\
          file1.txt \\
          file2.txt
    """
    # Replace \<newline><optional whitespace> with a single space
    return re.sub(r"\\\n[ \t]*", " ", text)


def to_script(text: str) -> Script:
    """
    Parse a command string into a Script AST node.

    Args:
        text: The shell command string.

    Returns:
        A Script node containing the parsed pipelines.

    Raises:
        ParseError: If the syntax is invalid.
    """
    if not text or not text.strip():
        return Script(pipelines=[])

    # 0. Handle line continuation (backslash-newline)
    text = _handle_line_continuation(text)

    # 1. Mask quoted strings to prevent shlex from stripping quotes
    # This preserves "'*'" as "'*'" in the token stream instead of "*"
    masked_text, mask_map = mask_quotes(text)

    # Configure shlex to handle shell punctuation as separate tokens
    # punctuation_chars=True ensures "ls|grep" becomes ["ls", "|", "grep"]
    lexer = shlex.shlex(masked_text, posix=True, punctuation_chars=True)

    # Treat newlines as tokens, not whitespace, so we can use them as separators
    lexer.whitespace = " \t\r"

    try:
        tokens = list(lexer)
    except ValueError as e:
        raise ParseError(f"Tokenization error: {e}") from e

    return _parse_tokens(tokens, mask_map)


def _parse_tokens(tokens: list[str], mask_map: dict[str, str]) -> Script:
    """
    Convert a list of tokens into a Script.

    Structure:
    Script = Pipeline { (";" | "&&" | "||" | NEWLINE) Pipeline }*
    Pipeline = Command { "|" Command }*
    Command = Word { Arg | Redirect }*
    """
    pipelines: list[Pipeline] = []
    operators: list[Operator] = []
    current_pipeline_cmds: list[Command] = []
    pending_op: Operator | None = None

    # Iterator for consumption
    it = iter(tokens)

    # Current command build state
    cmd_name: str | None = None
    cmd_args: list[str] = []
    cmd_redirects: list[Redirect] = []

    def flush_command():
        nonlocal cmd_name, cmd_args, cmd_redirects
        if cmd_name:
            current_pipeline_cmds.append(
                Command(name=cmd_name, args=cmd_args, redirects=cmd_redirects)
            )
        cmd_name = None
        cmd_args = []
        cmd_redirects = []

    def flush_pipeline(op: Operator):
        nonlocal current_pipeline_cmds, pending_op
        flush_command()
        if current_pipeline_cmds:
            if pending_op is not None:
                operators.append(pending_op)
            pipelines.append(Pipeline(commands=current_pipeline_cmds))
            pending_op = op
        current_pipeline_cmds = []

    def unmask(token: str) -> str:
        return unmask_quotes(token, mask_map)

    try:
        while True:
            token = next(it)

            if token in (";", "\n", "&&", "||"):
                op: Operator = ";" if token == "\n" else token  # type: ignore[assignment]
                flush_pipeline(op)
                continue

            elif token == "|":
                flush_command()
                if not current_pipeline_cmds and not cmd_name:
                    raise ParseError("Unexpected pipe '|' before command")
                continue

            elif token in (">", ">>", "<"):
                # Handle Redirect
                try:
                    target = next(it)
                    # Check if target is another operator
                    if target in (";", "|", ">", ">>", "<", "\n", "&&", "||"):
                        raise ParseError(
                            f"Expected filename after '{token}', got '{target}'"
                        )
                except StopIteration:
                    raise ParseError(f"Expected filename after '{token}'")

                # Unmask target filename
                target = unmask(target)
                cmd_redirects.append(Redirect(type=token, target=target))  # type: ignore[arg-type]
                continue

            else:
                # Regular word (Command Name or Argument)
                token = unmask(token)
                if cmd_name is None:
                    cmd_name = token
                else:
                    cmd_args.append(token)

    except StopIteration:
        pass

    # Final flush — use ";" as a dummy op (won't be appended since there's no next pipeline)
    flush_pipeline(";")

    return Script(pipelines=pipelines, operators=operators)
