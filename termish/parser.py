import re
import shlex

from .ast import Command, Operator, Pipeline, Redirect, Script
from .quote_masker import mask_quotes, unmask_quotes


class ParseError(Exception):
    """Raised when the parser encounters invalid syntax."""

    pass


_HD_KEY = "__termish_heredoc_{n}__"


def _find_heredoc_ops(line: str) -> list[int]:
    """Positions of ``<<`` operators OUTSIDE quotes in a raw line.

    Expansion happens at execution time, never at parse time, so quote
    state is a simple two-flag scan; backslash escapes the next char
    outside single quotes.
    Triple ``<<<`` (herestring) is not supported and is skipped so it
    falls through to the tokenizer as a normal parse problem.
    """
    positions: list[int] = []
    in_single = in_double = False
    i = 0
    while i < len(line):
        c = line[i]
        if c == "\\" and not in_single:
            i += 2
            continue
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif (
            c == "#"
            and not in_single
            and not in_double
            and (i == 0 or line[i - 1] in " \t")
        ):
            # Unquoted word-start '#' begins a comment — the tokenizer
            # (shlex commenters) strips it; the scanner must agree or
            # a '<<' inside a comment starts a phantom heredoc.
            break
        elif (
            c == "<"
            and not in_single
            and not in_double
            and line[i + 1 : i + 2] == "<"
            and line[i + 2 : i + 3] != "<"
            and line[i - 1 : i] != "<"
        ):
            positions.append(i)
            i += 2
            continue
        i += 1
    return positions


def _extract_heredocs(text: str) -> tuple[str, dict[str, tuple[str, str]]]:
    """Pull here-document bodies out of the raw text BEFORE tokenizing.

    ``cmd <<EOF`` / ``<<'EOF'`` / ``<<"EOF"`` — the operator span is
    replaced with ``<< __termish_heredoc_N__`` and the body (the lines
    following the command line, up to the delimiter line) is stored in
    the returned map as ``key -> (delimiter, body)``. Bodies are raw:
    never expanded, quotes and operators inert (bodies behave as if the
    delimiter were quoted, so quoted and unquoted delimiters are
    identical — unlike bash, which expands under unquoted delimiters).
    The delimiter line matches exactly or whitespace-stripped (agents
    indent). Multiple heredocs on one line consume bodies in order.

    Raises ParseError for a missing delimiter word or an unterminated
    heredoc.
    """
    if "<<" not in text:
        return text, {}

    heredocs: dict[str, tuple[str, str]] = {}
    out_lines: list[str] = []
    lines = text.split("\n")
    counter = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        # Join line continuations of the COMMAND line here (bodies below
        # are consumed raw — a trailing backslash inside a body must
        # survive, which is why the global continuation pass can't run
        # before extraction). Odd trailing backslashes = continuation.
        while (len(line) - len(line.rstrip("\\"))) % 2 == 1 and i + 1 < len(lines):
            i += 1
            line = line[:-1] + " " + lines[i].lstrip(" \t")
        pending: list[str] = []  # delimiters awaiting bodies, in order
        ops = _find_heredoc_ops(line)
        # rewrite right-to-left so positions stay valid
        for pos in reversed(ops):
            j = pos + 2
            while j < len(line) and line[j] in " \t":
                j += 1
            if j >= len(line):
                raise ParseError("Expected delimiter after '<<'")
            quote = line[j] if line[j] in "'\"" else ""
            if quote:
                end = line.find(quote, j + 1)
                if end == -1:
                    raise ParseError("Unterminated quote in heredoc delimiter")
                delim = line[j + 1 : end]
                j = end + 1
            else:
                end = j
                while end < len(line) and line[end] not in " \t|;&<>":
                    end += 1
                delim = line[j:end]
                j = end
            if not delim:
                raise ParseError("Expected delimiter after '<<'")
            key = _HD_KEY.format(n=counter)
            counter += 1
            line = f"{line[:pos]}<< {key}{line[j:]}"
            pending.insert(0, (key, delim))  # reversed scan -> restore order
        out_lines.append(line)
        i += 1
        for key, delim in pending:
            body_lines: list[str] = []
            while True:
                if i >= len(lines):
                    raise ParseError(
                        f"Unterminated heredoc: expected '{delim}' before end of input"
                    )
                candidate = lines[i]
                i += 1
                if candidate == delim or candidate.strip() == delim:
                    break
                body_lines.append(candidate)
            body = "\n".join(body_lines) + "\n" if body_lines else ""
            heredocs[key] = (delim, body)
    return "\n".join(out_lines), heredocs


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

    # 0a. Extract heredoc bodies (raw text — before continuation and
    # masking so bodies are preserved byte-for-byte)
    text, heredocs = _extract_heredocs(text)

    # 0b. Handle line continuation (backslash-newline)
    text = _handle_line_continuation(text)

    # 1. Mask quoted strings to prevent shlex from stripping quotes
    # This preserves "'*'" as "'*'" in the token stream instead of "*"
    masked_text, mask_map = mask_quotes(text)

    # Command substitution is unsupported; fail loudly rather than let
    # "$(cmd)" tokenize into mangled args ("$", "(", "cmd", ")") or pass
    # through unexpanded inside double quotes.  Single-quoted "$(...)"
    # stays a harmless literal, exactly as in bash.
    if "$(" in masked_text or any(v[0] == '"' and "$(" in v for v in mask_map.values()):
        raise ParseError(
            "Command substitution '$(...)' is not supported; "
            "run the inner command separately"
        )

    # Configure shlex to handle shell punctuation as separate tokens
    # punctuation_chars=True ensures "ls|grep" becomes ["ls", "|", "grep"]
    lexer = shlex.shlex(masked_text, posix=True, punctuation_chars=True)

    # shlex with punctuation_chars=True has a narrow wordchars set that
    # excludes several characters which are NOT shell operators and should
    # be treated as part of words.  Without this, "user@host" splits into
    # ["user", "@", "host"], "100%" into ["100", "%"], etc.
    # "$?{}" keeps variable forms ("$?", "$NAME", "${NAME}") intact as
    # single words for execution-time expansion.
    lexer.wordchars += ":@,%+!^$?{}"

    # Treat newlines as tokens, not whitespace, so we can use them as separators
    lexer.whitespace = " \t\r"

    try:
        tokens = list(lexer)
    except ValueError as e:
        raise ParseError(f"Tokenization error: {e}") from e

    return _parse_tokens(tokens, mask_map, heredocs)


def _parse_tokens(
    tokens: list[str],
    mask_map: dict[str, str],
    heredocs: dict[str, tuple[str, str]] | None = None,
) -> Script:
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
                # Check for trailing pipe
                try:
                    next_token = next(it)
                except StopIteration:
                    raise ParseError("Unexpected end of input after '|'")
                # Push back by handling the next token inline
                if next_token in ("|", ";", "\n", "&&", "||"):
                    raise ParseError(f"Expected command after '|', got '{next_token}'")
                # It's a regular token — start the next command
                next_token = unmask(next_token)
                cmd_name = next_token
                continue

            elif token == "<<":
                try:
                    key = next(it)
                except StopIteration:
                    raise ParseError("Expected delimiter after '<<'")
                entry = (heredocs or {}).get(key)
                if entry is None:
                    raise ParseError(f"Expected heredoc after '<<', got '{key}'")
                delim, body = entry
                cmd_redirects.append(Redirect(type="<<", target=delim, content=body))
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

                # Check if the preceding token was a stderr fd (e.g. "2" in
                # "2>/dev/null"). Only "2" is recognized — higher numbers
                # are regular args. The "2" may be at args[-1] (post-command,
                # e.g. ``cmd 2>file``) or at cmd_name itself when the redirect
                # leads the command (e.g. ``2>file cmd``).
                if cmd_args and cmd_args[-1] == "2":
                    is_stderr = True
                    cmd_args.pop()
                elif not cmd_args and cmd_name == "2":
                    is_stderr = True
                    cmd_name = None
                else:
                    is_stderr = False

                # Unmask target filename
                target = unmask(target)
                if is_stderr:
                    # stderr redirect: "2>file" / "2>>file"
                    cmd_redirects.append(
                        Redirect(type="2" + token, target=target)  # type: ignore[arg-type]
                    )
                else:
                    # stdout redirect (or fd 1)
                    cmd_redirects.append(Redirect(type=token, target=target))  # type: ignore[arg-type]
                continue

            elif token == ">&":
                # bash-style fd merge (e.g. "2>&1", ">&1").  "2>&1" becomes
                # a real Redirect node — the interpreter merges the command's
                # stderr into its stdout (into the pipe).  Other merges
                # ("1>&2", ">&1", "2>&2") stay no-ops: termish has no
                # separate stderr stream to merge INTO, so they're vacuous;
                # we recognize and discard rather than letting shlex's "2",
                # ">&", "1" tokens fall through as args.
                try:
                    target_fd = next(it)
                except StopIteration:
                    raise ParseError("Expected fd after '>&'")
                if target_fd in (";", "|", ">", ">>", "<", ">&", "\n", "&&", "||"):
                    raise ParseError(f"Expected fd after '>&', got '{target_fd}'")
                # Pop the leading source fd ("2" in "2>&1") if present.
                # May live at args[-1] (``cmd 2>&1``) or at cmd_name when the
                # merge leads the command (``2>&1 cmd``).
                source_fd = None
                if cmd_args and cmd_args[-1] in ("1", "2"):
                    source_fd = cmd_args.pop()
                elif not cmd_args and cmd_name in ("1", "2"):
                    source_fd = cmd_name
                    cmd_name = None
                if source_fd == "2" and target_fd == "1":
                    cmd_redirects.append(Redirect(type="2>&1", target="1"))
                # else: discard — nothing to merge.
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
