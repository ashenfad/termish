"""Parse shell script text into the AST in ``termish.ast``.

Shell control flow, functions, variable assignment, and command and
arithmetic substitution are not implemented. They fail here, before
anything runs, each with a diagnostic that says where it is and what to
do instead (rather than as several "command not found" lines), and
every one in the script at once, so a single retry can fix them all.
Each stage keeps, for every character it outputs, the offset in the
text it read, so a token's place in the script is known.
"""

from __future__ import annotations

import re
import shlex
import uuid
from typing import Callable, NoReturn

from .ast import Command, Operator, Pipeline, Redirect, Script
from .quote_masker import mask_quotes, unmask_quotes


class ParseError(Exception):
    """Raised when the parser encounters invalid syntax."""

    pass


_HD_KEY = "__termish_heredoc_{n}__"

_FUNCTION_ADVICE = (
    "function definitions are not supported; "
    "inject reusable commands through execute()'s 'commands' argument"
)
_LOOP_ADVICE = "control flow is not supported; use xargs or find -exec for iteration"
_CONDITIONAL_ADVICE = (
    "control flow is not supported; test with [ ... ] and branch with && and || instead"
)
_SUBSTITUTION_ADVICE = (
    "Command substitution '$(...)' is not supported; run the inner command separately"
)
_ARITHMETIC_ADVICE = (
    "arithmetic expansion $((...)) is not supported; compute it in run_python"
)
_ASSIGNMENT_ADVICE = (
    "variable assignment is not supported; "
    "write the value where it's used, or compute it in run_python"
)

#: A word that assigns a variable, as bash reads one in command position
#: (``N=3``, ``X="a b"``).
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")


class _Diagnostics:
    """What's wrong with a script, by where: offsets into the text given
    to ``to_script``, reported as lines and columns."""

    def __init__(self, text: str) -> None:
        self._text = text
        self._found: list[tuple[int, str]] = []

    def add(self, offset: int, message: str) -> None:
        self._found.append((offset, message))

    def fail(self, offset: int, message: str) -> NoReturn:
        """Add a problem nothing can be parsed past, and raise them all."""
        self.add(offset, message)
        self.raise_if_any()
        raise ParseError(message)

    def raise_if_any(self) -> None:
        """Raise every problem found, in the order they come in the script."""
        if not self._found:
            return
        lines: list[str] = []
        for offset, message in sorted(self._found, key=lambda f: f[0]):
            line = f"{self._place(offset)}: {message}"
            if line not in lines:
                lines.append(line)
        raise ParseError("\n".join(lines))

    def _place(self, offset: int) -> str:
        """``line L, col C``, both from 1."""
        before = self._text[:offset]
        return f"line {before.count(chr(10)) + 1}, col {offset - before.rfind(chr(10))}"


def _substitutions(text: str) -> list[tuple[int, str]]:
    """Where each ``$(`` and ``$((`` in ``text`` starts, unless escaped."""
    out: list[tuple[int, str]] = []
    i = 0
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text.startswith("$((", i):
            out.append((i, _ARITHMETIC_ADVICE))
            i += 3
            continue
        if text.startswith("$(", i):
            out.append((i, _SUBSTITUTION_ADVICE))
            i += 2
            continue
        i += 1
    return out


# A transform's output is traced: each character's offset in its input,
# and one past the end, for the end of the text.
_Traced = tuple[str, list[int]]


def _untraced(text: str) -> _Traced:
    return text, list(range(len(text) + 1))


def _trace_spans(text: str, spans: list[tuple[int, int, str]]) -> _Traced:
    """``text`` with each ``(start, end)`` span replaced by its string,
    traced: the replacement maps to its span's start."""
    out: list[str] = []
    origin: list[int] = []
    pos = 0
    for start, end, by in spans:
        out.append(text[pos:start])
        origin.extend(range(pos, start))
        out.append(by)
        origin.extend([start] * len(by))
        pos = end
    out.append(text[pos:])
    origin.extend(range(pos, len(text) + 1))
    return "".join(out), origin


_QUOTED = re.compile(r"__Q_[0-9a-f]+_\d+__")


def _trace_mask(before: str, masked: str, mask_map: dict[str, str]) -> list[int]:
    """Each character of ``masked``'s offset in ``before``, the text it
    masked: a placeholder maps to the start of the quoted span it stands
    for."""
    origin: list[int] = []
    i = j = 0
    while i < len(masked):
        m = _QUOTED.match(masked, i)
        if m is not None and m.group(0) in mask_map:
            origin.extend([j] * len(m.group(0)))
            i += len(m.group(0))
            j += len(mask_map[m.group(0)])
        else:
            origin.append(j)
            i += 1
            j += 1
    origin.append(len(before))
    return origin


#: What a word starts after: whitespace, a newline, or an operator
#: character. A ``#`` starts a comment only there, as in bash.
_WORD_BREAKS = frozenset(" \t\r\n;&|<>()")


def _comments(text: str) -> list[tuple[int, int]]:
    """The comments in ``text``, as ``(start, end)`` spans.

    A ``#`` starts a comment when it is unquoted, unescaped, and starts
    a word: at the beginning of a line, or after whitespace or an
    operator character (``echo hi;# note``). Inside a word it is a
    literal character (``a#b``, ``http://host/page#frag``, ``$#``). A
    comment runs to the end of its line, and quotes in it are inert
    (``# don't``). The heredoc scanner and the tokenizer both go by
    this, so neither sees a ``<<`` or ``$(`` in a comment.
    """
    spans: list[tuple[int, int]] = []
    in_single = in_double = False
    word_start = True
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\" and not in_single:
            i += 2
            word_start = False
            continue
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif c == "#" and word_start and not in_single and not in_double:
            end = text.find("\n", i)
            end = len(text) if end == -1 else end
            spans.append((i, end))
            i = end
            continue
        word_start = c in _WORD_BREAKS and not in_single and not in_double
        i += 1
    return spans


def _drop_comments(text: str) -> str:
    """``text`` without its comments; the newlines that end them stay."""
    return _trace_drop_comments(text)[0]


def _trace_drop_comments(text: str) -> _Traced:
    """``text`` without its comments, traced."""
    if "#" not in text:
        return _untraced(text)
    return _trace_spans(text, [(start, end, "") for start, end in _comments(text)])


def _find_heredoc_ops(line: str) -> list[int]:
    """Positions of ``<<`` operators OUTSIDE quotes in a raw line.

    Expansion happens at execution time, never at parse time, so quote
    state is a simple two-flag scan; backslash escapes the next char
    outside single quotes. A comment is cut off first: the tokenizer
    drops it, and a ``<<`` in it must not start a phantom heredoc.
    Triple ``<<<`` (herestring) is not supported and is skipped so it
    falls through to the tokenizer as a normal parse problem.
    """
    line = _drop_comments(line)
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


def _extract_heredocs(
    text: str, found: _Diagnostics
) -> tuple[str, list[int], dict[str, tuple[str, str]]]:
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

    Returns the text, traced, and the bodies. Reports a missing
    delimiter word or an unterminated heredoc.
    """
    if "<<" not in text:
        return (*_untraced(text), {})

    heredocs: dict[str, tuple[str, str]] = {}
    out_lines: list[str] = []
    out_origins: list[list[int]] = []
    lines = text.split("\n")
    line_starts: list[int] = []
    at = 0
    for raw in lines:
        line_starts.append(at)
        at += len(raw) + 1

    def span(k: int, start: int = 0) -> list[int]:
        return list(range(line_starts[k] + start, line_starts[k] + len(lines[k])))

    counter = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        origin = span(i)
        # Join line continuations of the COMMAND line here (bodies below
        # are consumed raw — a trailing backslash inside a body must
        # survive, which is why the global continuation pass can't run
        # before extraction). Odd trailing backslashes = continuation.
        while (len(line) - len(line.rstrip("\\"))) % 2 == 1 and i + 1 < len(lines):
            i += 1
            indent = len(lines[i]) - len(lines[i].lstrip(" \t"))
            line = line[:-1] + " " + lines[i][indent:]
            origin = origin + span(i, indent)
        # where the command line's newline is, for the output's
        line_end = line_starts[i] + len(lines[i])
        pending: list[tuple[str, str, int]] = []  # delimiters awaiting bodies
        ops = _find_heredoc_ops(line)
        # rewrite right-to-left so positions stay valid
        for pos in reversed(ops):
            here = origin[pos]
            j = pos + 2
            while j < len(line) and line[j] in " \t":
                j += 1
            if j >= len(line):
                found.fail(here, "Expected delimiter after '<<'")
            quote = line[j] if line[j] in "'\"" else ""
            if quote:
                end = line.find(quote, j + 1)
                if end == -1:
                    found.fail(origin[j], "Unterminated quote in heredoc delimiter")
                delim = line[j + 1 : end]
                j = end + 1
            else:
                end = j
                while end < len(line) and line[end] not in " \t|;&<>":
                    end += 1
                delim = line[j:end]
                j = end
            if not delim:
                found.fail(here, "Expected delimiter after '<<'")
            key = _HD_KEY.format(n=counter)
            counter += 1
            line = f"{line[:pos]}<< {key}{line[j:]}"
            origin = origin[:pos] + [here] * (3 + len(key)) + origin[j:]
            pending.insert(0, (key, delim, here))  # reversed scan -> restore order
        out_lines.append(line)
        out_origins.append(origin + [line_end])
        i += 1
        for key, delim, here in pending:
            body_lines: list[str] = []
            while True:
                if i >= len(lines):
                    found.fail(
                        here,
                        f"Unterminated heredoc: expected '{delim}' before end of input",
                    )
                candidate = lines[i]
                i += 1
                if candidate == delim or candidate.strip() == delim:
                    break
                body_lines.append(candidate)
            body = "\n".join(body_lines) + "\n" if body_lines else ""
            heredocs[key] = (delim, body)
    # Each line's origin ends with its newline's offset. The last line's
    # newline isn't output: its entry stands for the end of the text.
    traced = [o for origin in out_origins for o in origin]
    traced[-1] = len(text)
    return "\n".join(out_lines), traced, heredocs


def _handle_line_continuation(text: str) -> _Traced:
    """Remove backslash-newline sequences (line continuation).

    In shell, a backslash followed by a newline joins lines together.
    We also strip leading whitespace from the continuation line to match
    common usage patterns like:

        git add \\
          file1.txt \\
          file2.txt
    """
    # Replace \<newline><optional whitespace> with a single space
    spans = [(m.start(), m.end(), " ") for m in re.finditer(r"\\\n[ \t]*", text)]
    return _trace_spans(text, spans)


#: Characters that mean something bare: operators, globs, quotes, a
#: comment's start, brace expansion.
_MEANINGFUL = frozenset("|;<>&*?[]'\"#{},")


def _keep_escapes(text: str, mask_map: dict[str, str]) -> str:
    r"""Protect the backslash of an unquoted ``\$`` or ``\\`` from shlex,
    and quote an escaped character that means something bare.

    shlex (posix) drops the backslash of every escape, but expansion
    runs after tokenizing and needs it to tell ``\$NAME`` (a literal
    ``$NAME``) from ``$NAME``, and ``\\$NAME`` (a backslash, then an
    expansion) from ``\$NAME``. Doubling those two escapes makes shlex
    hand the original pair through, and expansion resolves it, as it
    already does inside double quotes. An escaped character that means
    something bare (an operator, a glob, a quote, a comment's start,
    brace expansion) is masked as the quoted form of itself (added to
    ``mask_map``), so it is a literal everywhere a quoted one is: ``\;``
    is a word, never the separator, and ``\*`` never globs. Every other
    escape is left for shlex to resolve. ``text`` is quote-masked, so
    every backslash in it is unquoted.
    """
    if "\\" not in text:
        return text
    session = uuid.uuid4().hex[:8]
    out: list[str] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt in "$\\":
                # an escaped backslash, then the escape itself: shlex
                # reads the two back as the original backslash and nxt
                out.append("\\\\" + "\\" + nxt)
            elif nxt in _MEANINGFUL:
                key = "__E_" + session + "_" + str(len(mask_map)) + "__"
                mask_map[key] = '"\'"' if nxt == "'" else "'" + nxt + "'"
                out.append(key)
            else:
                out.append(c + nxt)
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _token_starts(text: str, lexer: shlex.shlex) -> list[int]:
    """Where each token ``lexer`` makes of ``text`` starts, by its rules:
    a word is a run of its word characters and escapes, a run of its
    punctuation characters is one token, and a newline or any other
    character is a token of its own."""
    starts: list[int] = []
    words, punctuation = lexer.wordchars, lexer.punctuation_chars
    i = 0
    while i < len(text):
        c = text[i]
        if c in " \t\r":
            i += 1
            continue
        starts.append(i)
        if c in punctuation:
            while i < len(text) and text[i] in punctuation:
                i += 1
        elif c in words or c == "\\":
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                elif text[i] in words:
                    i += 1
                else:
                    break
        else:
            i += 1
    return starts


def to_script(text: str) -> Script:
    """
    Parse a command string into a Script AST node.

    Args:
        text: The shell command string.

    Returns:
        A Script node containing the parsed pipelines.

    Raises:
        ParseError: If the syntax is invalid, or the script uses what
            termish doesn't support; the message says where each is.
    """
    if not text or not text.strip():
        return Script(pipelines=[])
    found = _Diagnostics(text)

    # 0a. Extract heredoc bodies (raw text — before continuation and
    # masking so bodies are preserved byte-for-byte)
    extracted, extracted_origin, heredocs = _extract_heredocs(text, found)

    # 0b. Handle line continuation (backslash-newline)
    joined, joined_origin = _handle_line_continuation(extracted)

    # 0c. Drop comments here rather than in shlex, whose commenters
    # start one at any '#', even mid-word, and before masking, which
    # would pair the apostrophe in "# don't" with a quote on a later line.
    uncommented, uncommented_origin = _trace_drop_comments(joined)

    # 1. Mask quoted strings to prevent shlex from stripping quotes
    # This preserves "'*'" as "'*'" in the token stream instead of "*"
    masked_text, mask_map = mask_quotes(uncommented)
    mask_origin = _trace_mask(uncommented, masked_text, mask_map)

    def to_input(i: int) -> int:
        return extracted_origin[joined_origin[uncommented_origin[mask_origin[i]]]]

    # Command and arithmetic substitution are unsupported; report them
    # rather than let "$(cmd)" tokenize into mangled args ("$", "(",
    # "cmd", ")") or pass through unexpanded inside double quotes.
    # Checked once comments are gone, so one may mention it.
    # Single-quoted "$(...)" and an escaped "\$(" stay harmless literals,
    # exactly as in bash.
    for index, message in _substitutions(masked_text):
        found.add(to_input(index), message)
    for key in _QUOTED.finditer(masked_text):
        original = mask_map.get(key.group(0), "")
        if original[:1] == '"':
            at = to_input(key.start())
            for index, message in _substitutions(original):
                found.add(at + index, message)

    # Configure shlex to handle shell punctuation as separate tokens
    # punctuation_chars=True ensures "ls|grep" becomes ["ls", "|", "grep"]
    lexer = shlex.shlex(
        _keep_escapes(masked_text, mask_map), posix=True, punctuation_chars=True
    )

    # shlex with punctuation_chars=True has a narrow wordchars set that
    # excludes several characters which are NOT shell operators and should
    # be treated as part of words.  Without this, "user@host" splits into
    # ["user", "@", "host"], "100%" into ["100", "%"], etc.
    # "$?{}" keeps variable forms ("$?", "$NAME", "${NAME}") intact as
    # single words for execution-time expansion. "[]" keeps a glob's
    # class ("a/[bc]*") one word; `[ -f x ]` still splits on its spaces.
    lexer.wordchars += ":@,%+!^$?{}[]#"
    # Comments are already gone; a '#' left is part of a word.
    lexer.commenters = ""

    # Treat newlines as tokens, not whitespace, so we can use them as separators
    lexer.whitespace = " \t\r"

    try:
        tokens = list(lexer)
    except ValueError as e:
        raise ParseError(f"Tokenization error: {e}") from e

    # Where each token is: by the lexer's rules, over the masked text. If
    # the two ever disagree on how many tokens there are, a problem is
    # placed at the start of the script rather than at the wrong token.
    starts = _token_starts(masked_text, lexer)
    if len(starts) != len(tokens):
        starts = [0] * len(tokens)

    script = _parse_tokens(
        tokens, mask_map, heredocs, lambda t: to_input(starts[t]), found
    )
    found.raise_if_any()
    return script


def _parse_tokens(
    tokens: list[str],
    mask_map: dict[str, str],
    heredocs: dict[str, tuple[str, str]] | None,
    at: Callable[[int], int],
    found: _Diagnostics,
) -> Script:
    """
    Convert a list of tokens into a Script.

    Structure:
    Script = Pipeline { (";" | "&&" | "||" | NEWLINE) Pipeline }*
    Pipeline = Command { "|" Command }*
    Command = Word { Arg | Redirect }*

    ``at`` gives a token's offset in the script, by its index, for the
    problems ``found`` reports.
    """
    pipelines: list[Pipeline] = []
    operators: list[Operator] = []
    current_pipeline_cmds: list[Command] = []
    pending_op: Operator | None = None

    # Current command build state
    cmd_name: str | None = None
    cmd_start = 0
    cmd_args: list[str] = []
    cmd_redirects: list[Redirect] = []

    # The control-flow blocks open, innermost last: a block's opener is
    # reported, and the words that continue and close it ("do", "done",
    # "then", "fi", "esac") are part of that report.
    blocks: list[str] = []

    def unmask(token: str) -> str:
        return unmask_quotes(token, mask_map)

    def check_command_word(word: str, t: int) -> None:
        """Report ``word`` (masked, in command position, token ``t``) if
        it's control flow, ``function``, or an assignment. Elsewhere
        these are ordinary arguments (``echo for``, ``grep -r done .``),
        and a quoted word never reaches here as itself: it is an opaque
        placeholder."""
        top = blocks[-1] if blocks else None

        def report(message: str) -> None:
            found.add(at(t), f"{word}: {message}")

        if word in ("for", "while", "until"):
            report(_LOOP_ADVICE)
            blocks.append("loop")
        elif word in ("do", "done"):
            if top != "loop":
                report(_LOOP_ADVICE)
            elif word == "done":
                blocks.pop()
        elif word == "if":
            report(_CONDITIONAL_ADVICE)
            blocks.append("if")
        elif word in ("then", "elif", "else", "fi"):
            if top != "if":
                report(_CONDITIONAL_ADVICE)
            elif word == "fi":
                blocks.pop()
        elif word == "case":
            report("control flow is not supported")
            blocks.append("case")
        elif word == "esac":
            if top != "case":
                report("control flow is not supported")
            else:
                blocks.pop()
        elif word == "function":
            report(_FUNCTION_ADVICE)
        elif _ASSIGNMENT.match(unmask(word)):
            found.add(at(t), f"{unmask(word)}: {_ASSIGNMENT_ADVICE}")

    def flush_command():
        nonlocal cmd_name, cmd_args, cmd_redirects
        if cmd_name:
            # ``name() { ... }`` -- the parenthesis pair follows the name
            # with no argument between, as either one token ("()") or two
            # ("(", ")").  A lone "(" elsewhere is left alone.
            if cmd_args[:1] == ["()"] or cmd_args[:2] == ["(", ")"]:
                found.add(cmd_start, f"{cmd_name}(): {_FUNCTION_ADVICE}")
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

    i = 0
    while i < len(tokens):
        token = tokens[i]
        here = at(i)
        i += 1

        if token in (";", "\n", "&&", "||"):
            op: Operator = ";" if token == "\n" else token  # type: ignore[assignment]
            flush_pipeline(op)
            continue

        elif token == "|":
            flush_command()
            if not current_pipeline_cmds and not cmd_name:
                found.fail(here, "Unexpected pipe '|' before command")
            # Check for trailing pipe
            if i >= len(tokens):
                found.fail(here, "Unexpected end of input after '|'")
            next_token = tokens[i]
            i += 1
            if next_token in ("|", ";", "\n", "&&", "||"):
                found.fail(at(i - 1), f"Expected command after '|', got '{next_token}'")
            # It's a regular token — start the next command
            check_command_word(next_token, i - 1)
            cmd_name = unmask(next_token)
            cmd_start = at(i - 1)
            continue

        elif token == "<<":
            if i >= len(tokens):
                found.fail(here, "Expected delimiter after '<<'")
            key = tokens[i]
            i += 1
            entry = (heredocs or {}).get(key)
            if entry is None:
                found.fail(here, f"Expected heredoc after '<<', got '{key}'")
            delim, body = entry
            cmd_redirects.append(Redirect(type="<<", target=delim, content=body))
            continue

        elif token in (">", ">>", "<"):
            # Handle Redirect
            if i >= len(tokens):
                found.fail(here, f"Expected filename after '{token}'")
            target = tokens[i]
            i += 1
            # Check if target is another operator
            if target in (";", "|", ">", ">>", "<", "\n", "&&", "||"):
                found.fail(here, f"Expected filename after '{token}', got '{target}'")

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
            if i >= len(tokens):
                found.fail(here, "Expected fd after '>&'")
            target_fd = tokens[i]
            i += 1
            if target_fd in (";", "|", ">", ">>", "<", ">&", "\n", "&&", "||"):
                found.fail(here, f"Expected fd after '>&', got '{target_fd}'")
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
            if cmd_name is None:
                check_command_word(token, i - 1)
                cmd_name = unmask(token)
                cmd_start = here
            else:
                cmd_args.append(unmask(token))

    # Final flush — use ";" as a dummy op (won't be appended since there's no next pipeline)
    flush_pipeline(";")

    return Script(pipelines=pipelines, operators=operators)
