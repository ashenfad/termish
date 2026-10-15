"""
Core interpreter logic for executing terminal scripts against a FileSystem.
Functional implementation.

Each command's words expand braces, then ``$NAME``, ``${NAME}`` and
``$?``, then globs, as in bash. ``$PWD`` is the current directory,
whatever the environment says. A variable that isn't set is an error
that stops the script, as under bash's ``set -u``: with no assignment in
the language, every variable comes from the host, so an unset one is a
mistake, and ``cd $DIR`` or ``rm -r $DIR/x`` must not run as ``cd`` or
``rm -r /x``.
"""

import contextvars
import io
import re
from collections.abc import Callable, Mapping

from termish.ast import Pipeline, Script
from termish.context import CommandContext, PipeStream
from termish.errors import CommandFunc, TerminalError
from termish.fs import FileSystem
from termish.quote_masker import mask_quotes, unmask_and_unquote

from .brace import expand_braces, parameter_end
from .commands import (
    archive,
    checksum,
    clock,
    conditional,
    control,
    filesystem,
    fsinfo,
    info,
    lines,
    meta,
    search,
    text,
)
from .commands import diff as diff_cmd
from .commands import file as file_mod
from .commands import io as io_cmds
from .commands import jq as jq_cmd
from .commands import sed as sed_cmd
from .commands._util import resolve_path
from .pathglob import expand as expand_pathname
from .pathglob import has_magic

#: The redirect target that discards: written to, nothing is kept;
#: read from, it is empty. Never a file in the filesystem. A target is
#: compared once resolved, so ``dev/null`` from ``/`` and
#: ``/dev/./null`` are it too (compared as spelled, they would be
#: written as a file).
DEV_NULL = "/dev/null"


# Context var holding injected commands for the current execution.
# Set by execute_script() so that meta-commands like xargs can resolve
# injected commands without threading a parameter through every call.
_injected_commands: contextvars.ContextVar[Mapping[str, CommandFunc]] = (
    contextvars.ContextVar("_injected_commands", default={})
)


class _UnboundVariable(TerminalError):
    """``$NAME`` with ``NAME`` unset: the command doesn't run, nor
    anything after it."""

    def __init__(self, name: str):
        super().__init__(f"{name}: unbound variable")


def result_stderr(result: object) -> str:
    """A command result's stderr text, or "" for none or no result."""
    return getattr(result, "stderr", "") or ""


def _resolve_command(name: str) -> CommandFunc | None:
    """Look up a command by name: injected commands override built-ins."""
    injected = _injected_commands.get()
    if name in injected:
        return injected[name]
    return BUILTINS.get(name)


# Static mapping of built-in commands
BUILTINS: dict[str, CommandFunc] = {
    # Filesystem
    "pwd": filesystem.pwd,
    "cd": filesystem.cd,
    "mkdir": filesystem.mkdir,
    "ls": filesystem.ls,
    "touch": filesystem.touch,
    "cp": filesystem.cp,
    "mv": filesystem.mv,
    "rm": filesystem.rm,
    "basename": filesystem.basename,
    "dirname": filesystem.dirname,
    # I/O
    "echo": io_cmds.echo,
    "printf": io_cmds.printf,
    "cat": io_cmds.cat,
    "head": io_cmds.head,
    "tail": io_cmds.tail,
    "tee": io_cmds.tee,
    # Search
    "grep": search.grep,
    "find": search.find,
    # Text processing
    "wc": text.wc,
    "sort": text.sort,
    "uniq": text.uniq,
    "cut": text.cut,
    "sed": sed_cmd.sed,
    "tr": text.tr,
    # Diff
    "diff": diff_cmd.diff,
    # Meta
    "xargs": meta.xargs,
    # JSON
    "jq": jq_cmd.jq,
    # Archive
    "tar": archive.tar,
    "gzip": archive.gzip,
    "gunzip": archive.gunzip,
    "zcat": archive.zcat,
    "gzcat": archive.zcat,
    "zip": archive.zip_cmd,
    "unzip": archive.unzip,
    # Inspection
    "file": file_mod.file_cmd,
    "du": fsinfo.du,
    "stat": fsinfo.stat,
    "realpath": fsinfo.realpath,
    "md5sum": checksum.md5sum,
    "sha256sum": checksum.sha256sum,
    # Lines
    "seq": lines.seq,
    "paste": lines.paste,
    "comm": lines.comm,
    "rev": lines.rev,
    # Time
    "date": clock.date,
    # What's here
    "which": info.which,
    "command": info.command,
    "help": info.help_cmd,
    # Control
    "true": control.true_cmd,
    "false": control.false_cmd,
    "test": conditional.test_cmd,
    "[": conditional.bracket_cmd,
}


def execute_script(
    script: Script,
    fs: FileSystem,
    commands: Mapping[str, CommandFunc] | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """
    Execute a full script and return the final stdout.

    Operators between pipelines control execution flow:
    - ``;``  — always execute next pipeline
    - ``&&`` — execute next only if previous succeeded
    - ``||`` — execute next only if previous failed

    Args:
        script: The parsed AST.
        fs: The filesystem to operate on.
        commands: Optional mapping of injected command handlers.
            Injected commands override built-ins when names collide.
            Defaults to no injected commands.
        env: Optional environment variables for ``$VAR`` expansion.
            The dict is shared with command handlers via ``ctx.env``,
            so handler mutations are visible to later commands and to
            the caller. Defaults to an empty environment. A host should
            set ``HOME``; ``$PWD`` is always the current directory.

    Returns:
        The terminal transcript as a string: captured stdout, plus
        stderr diagnostics from failures that execution continued past
        and from success-with-stderr warnings.

    Raises:
        TerminalError: If the last executed pipeline failed (contains partial output).
    """
    if env is None:
        env = {}

    # Only set the context var if commands is explicitly provided.
    # When None, nested calls inherit the parent's injected commands.
    if commands is None:
        return _execute_script_inner(script, fs, env)

    token = _injected_commands.set(commands)
    try:
        return _execute_script_inner(script, fs, env)
    finally:
        _injected_commands.reset(token)


def _diagnostic(e: TerminalError) -> str:
    """Terminal-visible stderr text for a failure ('' if it failed silently)."""
    text = e.message if e.stderr is None else e.stderr
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def _execute_script_inner(script: Script, fs: FileSystem, env: dict[str, str]) -> str:
    """Inner execution loop (injected commands already set via context var)."""
    # The transcript accumulates bytes — whatever the commands actually
    # emitted — and is decoded once, at the end.  Diagnostics are text
    # and are encoded as they are appended.
    final_output = io.BytesIO()
    last_succeeded = True
    last_error: TerminalError | None = None
    last_exit_code = 0  # what "$?" expands to
    # Diagnostic from the most recent failure, not yet shown.  A terminal
    # prints stderr as it happens; we surface it in the transcript once
    # execution demonstrably continues past the failure.  If nothing runs
    # afterwards, the failure is the script's outcome and the diagnostic
    # travels via the raised TerminalError instead (no duplication).
    pending_diag = ""

    for i, pipeline in enumerate(script.pipelines):
        # Determine whether to execute this pipeline based on the preceding operator
        if i > 0:
            op = script.operators[i - 1]
            if op == "&&" and not last_succeeded:
                continue
            elif op == "||" and last_succeeded:
                continue
            # ";" always executes

        if pending_diag:
            final_output.write(pending_diag.encode("utf-8"))
        pending_diag = ""

        try:
            _execute_pipeline(pipeline, fs, final_output, env, last_exit_code)
            last_succeeded = True
            last_error = None
            last_exit_code = 0
        except _UnboundVariable as e:
            raise TerminalError(
                e.message,
                partial_output=_transcript(final_output),
                exit_code=e.exit_code,
            ) from None
        except TerminalError as e:
            last_succeeded = False
            last_error = e
            last_exit_code = e.exit_code
            pending_diag = _diagnostic(e)
        except Exception as e:
            last_succeeded = False
            last_error = TerminalError(f"Unexpected error: {e}")
            last_exit_code = 1
            pending_diag = _diagnostic(last_error)

    if last_error is not None:
        raise TerminalError(
            last_error.message,
            partial_output=_transcript(final_output),
            exit_code=last_error.exit_code,
            stderr=last_error.stderr,
        )

    return _transcript(final_output)


def _transcript(output: io.BytesIO) -> str:
    """Decode accumulated output for display.

    The transcript is what a terminal screen would have shown, so bytes
    that are not valid UTF-8 become U+FFFD here rather than raising.
    This is the only place the pipeline's payload is decoded on its way
    out; a byte that reached a file or a pipe reached it unchanged.
    """
    return output.getvalue().decode("utf-8", errors="replace")


def _execute_pipeline(
    pipeline: Pipeline,
    fs: FileSystem,
    stdout: io.BytesIO,
    env: dict[str, str],
    last_exit_code: int,
):
    """
    Execute a chain of commands.
    Raises TerminalError on failure.
    """
    if not pipeline.commands:
        return

    current_input: bytes | None = None
    merged_failure: TerminalError | None = None

    last_stage = len(pipeline.commands) - 1
    for stage, cmd_node in enumerate(pipeline.commands):
        cmd_stdin = PipeStream(current_input or b"")
        cmd_stdout = PipeStream()

        # Expand the command name and args as one word list so an
        # empty-expanding name shifts away (zsh-style: `$EMPTY echo hi`
        # runs `echo hi`).  Multi-word values do NOT field-split —
        # deliberately zsh, not bash: `CMD="echo hello"; $CMD` is
        # "echo hello: command not found", never a silent re-parse.
        variables = _variables(env, fs)
        words = _expand_args(
            [cmd_node.name] + cmd_node.args, fs, variables, last_exit_code
        )
        if not words:
            # The whole command expanded to nothing (`$EMPTY` alone):
            # a silent no-op, like zsh.  Redirects are not processed.
            merged_failure = None
            current_input = b""
            continue
        cmd_name, expanded_args = words[0], words[1:]

        # Every redirect's target is expanded and resolved before the
        # command runs, as in bash: an unset variable or an ambiguous
        # target stops it before it can do anything.
        paths: dict[int, tuple[str, str]] = {}
        for r in cmd_node.redirects:
            if r.type not in ("<<", "2>&1"):
                target = _expand_word(r.target, variables, last_exit_code)
                paths[id(r)] = (target, resolve_path(target, fs))

        # Handle Redirects (Input) — last input-ish redirect wins (bash)
        input_redirect = next(
            (r for r in reversed(cmd_node.redirects) if r.type in ("<", "<<")),
            None,
        )
        if input_redirect is not None:
            if input_redirect.type == "<<":
                # Heredoc bodies are always literal (as if the delimiter
                # were quoted) — no expansion.
                cmd_stdin = PipeStream((input_redirect.content or "").encode("utf-8"))
            else:
                target, path = paths[id(input_redirect)]
                if path == DEV_NULL:
                    # Reads as empty, as in bash: there is no device to
                    # open, and no file of that name should be needed.
                    cmd_stdin = PipeStream(b"")
                else:
                    try:
                        cmd_stdin = PipeStream(fs.read(path))
                    except Exception as e:
                        raise TerminalError(f"{cmd_name}: {target}: {e}")

        # Stderr routing — last stderr redirect wins (bash)
        stderr_redirect = next(
            (
                r
                for r in reversed(cmd_node.redirects)
                if r.type in ("2>", "2>>", "2>&1")
            ),
            None,
        )
        output_redirects = [r for r in cmd_node.redirects if r.type in (">", ">>")]

        def write_redirects(data: bytes) -> None:
            """Write ``data`` to each of the command's output redirects."""
            for r in output_redirects:
                _, path = paths[id(r)]
                if path == DEV_NULL:
                    # Discarded, as stderr's 2>/dev/null already was.
                    # Written as a file it lands in the filesystem as
                    # /dev/null, where every later listing and diff of
                    # the tree finds it.
                    continue
                try:
                    _write_to_file(path, data, r.type == ">>", fs)
                except Exception as e:
                    raise TerminalError(f"{cmd_name}: redirect failed: {e}")

        def drain_partial() -> None:
            """Send a failing command's own stdout where it was sent.

            A command writes into its private ``cmd_stdout``, which is
            normally drained further down (pipe it onward, or write it
            out). Raising jumps past that drain, so anything the command
            printed before it failed would vanish -- but a real shell
            still delivers it: an HTTP client's error body, written
            before it exits nonzero on the status, explains the failure.
            With an output redirect it goes there, as in bash (``cat a
            missing > out`` writes a's content to ``out``); without one,
            to the transcript. The failure stays the outcome even if the
            redirect cannot be written.
            """
            data = cmd_stdout.getvalue()
            if not output_redirects:
                stdout.write(data)
                return
            try:
                write_redirects(data)
            except TerminalError:
                pass

        # Execute Command — injected commands override built-ins
        failure: TerminalError | None = None
        result = None
        try:
            cmd_func = _resolve_command(cmd_name)
            if cmd_func is None:
                raise TerminalError(f"{cmd_name}: command not found", exit_code=127)
            ctx = CommandContext(
                args=expanded_args,
                stdin=cmd_stdin,
                stdout=cmd_stdout,
                fs=fs,
                env=env,
            )
            result = cmd_func(ctx)
            # A stage before the last that fails SILENTLY — grep that
            # matched nothing, false — hands its output on and the
            # pipeline goes on, as bash's does without pipefail: the
            # pipeline's status is its last stage's. `grep x f | wc -l`
            # prints 0. A failure with a diagnostic still stops it.
            silent_mid = stage != last_stage and not result_stderr(result)
            if result is not None and result.exit_code != 0 and not silent_mid:
                raise TerminalError(
                    f"{cmd_name}: {result.stderr}"
                    if result.stderr
                    else f"{cmd_name}: exited with code {result.exit_code}",
                    exit_code=result.exit_code,
                    stderr=result.stderr,
                )
        except TerminalError as e:
            if stderr_redirect is None:
                drain_partial()
                raise
            failure = e
        except Exception as e:
            wrapped = TerminalError(f"{cmd_name}: execution error: {e}")
            if stderr_redirect is None:
                drain_partial()
                raise wrapped
            failure = wrapped

        # This command's stderr text ('' if none / silent failure)
        if failure is not None:
            err_text = _diagnostic(failure)
        elif result is not None and result.stderr:
            err_text = (
                result.stderr if result.stderr.endswith("\n") else result.stderr + "\n"
            )
        else:
            err_text = ""

        # Route stderr
        if stderr_redirect is None:
            if err_text:
                # Success with diagnostics (e.g. warnings): a terminal
                # shows stderr on screen but never feeds it to the next
                # pipe stage — write it straight to the transcript.
                stdout.write(err_text.encode("utf-8"))
        elif stderr_redirect.type == "2>&1":
            # Merge into stdout: joins the pipe / transcript below.
            # Stderr is text; it enters the byte stream encoded UTF-8,
            # after whatever the handler wrote.
            cmd_stdout.flush()
            cmd_stdout.buffer.write(err_text.encode("utf-8"))
        else:
            # 2>file truncates (even when no stderr was produced, as in
            # bash); 2>>file appends; /dev/null discards.
            _, path = paths[id(stderr_redirect)]
            if path != DEV_NULL:
                try:
                    _write_to_file(
                        path,
                        err_text.encode("utf-8"),
                        stderr_redirect.type == "2>>",
                        fs,
                    )
                except Exception as e:
                    raise TerminalError(f"{cmd_name}: redirect failed: {e}")

        # A failure whose stderr went to a file still aborts the pipeline
        # (silently — its diagnostic was consumed by the redirect).  A
        # "2>&1" failure keeps the pipeline going so downstream stages see
        # the merged text (the agent idiom ``cmd 2>&1 | head``); if it's
        # the final stage, the failure resurfaces after the loop.
        if failure is not None and stderr_redirect.type != "2>&1":
            drain_partial()
            raise TerminalError(failure.message, exit_code=failure.exit_code, stderr="")
        merged_failure = failure  # only ever non-None for "2>&1"

        # Capture output
        output_bytes = cmd_stdout.getvalue()

        # Handle Output Redirects
        if output_redirects:
            write_redirects(output_bytes)
            current_input = b""
        else:
            current_input = output_bytes

    if current_input:
        stdout.write(current_input)

    # A "2>&1" failure in the FINAL stage determines the pipeline's exit
    # (bash without pipefail: earlier merged failures are superseded by
    # later stages).  Its diagnostic already reached the transcript or an
    # output redirect via the merge, so the raise itself is silent.
    if merged_failure is not None:
        raise TerminalError(
            merged_failure.message,
            exit_code=merged_failure.exit_code,
            stderr="",
        )


# Variable forms recognized at expansion time.  Anything else involving
# '$' (e.g. "$(", "$1", "$$", a trailing "grep foo$" anchor) is left
# literal — visible non-support beats silent mangling.  The escapes
# implement the double-quote backslash rule: backslash is special only
# before '\' or '$' (so "\d" in a regex stays "\d", while "\\$NAME"
# is an escaped backslash followed by a live expansion).
# "${NAME:-word}" and "${NAME-word}" give "word" (itself expanded) for a
# variable that's unset ("-"), or unset or empty (":-"): the way to read
# one that may not be set.  "word" runs to the "}" that closes its "${",
# as in bash, so it may hold another ("${A:-${B}}").
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_BRACED_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:\}|(:?-))")

#: A variable's value, or None when it isn't set.
Variables = Callable[[str], "str | None"]


def _variables(env: dict[str, str], fs: FileSystem) -> Variables:
    """The variables one command expands with: ``env``'s, with ``PWD``
    the current directory (asked for only when it's read)."""
    return lambda name: fs.getcwd() if name == "PWD" else env.get(name)


def _expand_vars(text: str, variables: Variables, last_exit_code: int) -> str:
    """Expand ``$?``, ``$NAME``, ``${NAME}`` and ``${NAME:-word}`` in text.

    An unset variable with no default raises ``_UnboundVariable``.
    ``\\$`` suppresses expansion and yields a literal ``$``; ``\\\\``
    yields a literal backslash (so ``\\\\$NAME`` is a backslash plus
    expansion).
    """
    if "$" not in text and "\\\\" not in text:
        return text
    out: list[str] = []
    i = 0
    while i < len(text):
        c = text[i]
        nxt = text[i + 1 : i + 2]
        if c == "\\" and nxt in ("\\", "$"):
            out.append(nxt)
            i += 2
        elif c != "$":
            out.append(c)
            i += 1
        elif nxt == "?":
            out.append(str(last_exit_code))
            i += 2
        elif nxt == "{":
            param = _expand_parameter(text, i, variables, last_exit_code)
            if param is None:
                out.append(c)
                i += 1
            else:
                out.append(param[0])
                i = param[1]
        else:
            m = _NAME_RE.match(text, i + 1)
            if m is None:
                out.append(c)
                i += 1
            else:
                out.append(_lookup(m.group(0), variables))
                i = m.end()
    return "".join(out)


def _expand_parameter(
    text: str, at: int, variables: Variables, last_exit_code: int
) -> tuple[str, int] | None:
    """The ``${NAME}`` or ``${NAME:-word}`` at ``at`` expanded, and the
    index after it; None for any other ``${`` form, which stays literal."""
    m = _BRACED_RE.match(text, at)
    if m is None:
        return None
    name, op = m.group(1), m.group(2)
    if op is None:
        return _lookup(name, variables), m.end()
    end = parameter_end(text, at)
    if end < 0:
        return None
    value = variables(name)
    if value is None or (op == ":-" and value == ""):
        word = text[m.end() : end]
        return _expand_vars(word, variables, last_exit_code), end + 1
    return value, end + 1


def _lookup(name: str, variables: Variables) -> str:
    """``name``'s value; ``_UnboundVariable`` when it isn't set."""
    value = variables(name)
    if value is None:
        raise _UnboundVariable(name)
    return value


def _expand_masked(
    masked: str,
    mask_map: dict[str, str],
    variables: Variables,
    last_exit_code: int,
) -> str:
    """Variable-expand a quote-masked word, honoring quote semantics.

    The unquoted portion and the contents of double-quoted regions are
    expanded; single-quoted regions stay literal.  Returns the masked
    text (mask_map is updated in place for expanded regions).
    """
    masked = _expand_vars(masked, variables, last_exit_code)
    for token, original in mask_map.items():
        if original[0] == '"':
            inner = _expand_vars(original[1:-1], variables, last_exit_code)
            mask_map[token] = '"' + inner + '"'
    return masked


def _braced(masked: str, mask_map: dict[str, str]) -> list[tuple[str, dict[str, str]]]:
    """``masked``'s brace expansion, each word with the placeholders it
    holds (a map of its own, as expansion rewrites the double-quoted
    ones)."""
    return [
        (word, {k: v for k, v in mask_map.items() if k in word})
        for word in expand_braces(masked)
    ]


def _expand_word(word: str, variables: Variables, last_exit_code: int) -> str:
    """Expand braces and variables in a single word (a redirect target)
    and strip quotes.  No globbing, no word removal; braces that make
    more than one word are an error, as in bash."""
    masked, mask_map = mask_quotes(word)
    words = _braced(masked, mask_map)
    if len(words) != 1:
        raise TerminalError(f"{word}: ambiguous redirect")
    only, own = words[0]
    return unmask_and_unquote(_expand_masked(only, own, variables, last_exit_code), own)


def _expand_args(
    args: list[str],
    fs: FileSystem,
    variables: Variables,
    last_exit_code: int,
) -> list[str]:
    """Expand braces, then variables, then glob, as bash does.

    Variables come before globs (so ``$?`` never reads as a glob ``?``);
    fully-unquoted args containing wildcards are globbed.  An unquoted
    arg that expands to nothing is removed entirely (bash word removal:
    ``echo a $EMPTY b`` has two args, not three).
    """
    expanded: list[str] = []
    for arg in args:
        masked_arg, quoted = mask_quotes(arg)
        for word, mask_map in _braced(masked_arg, quoted):
            masked = _expand_masked(word, mask_map, variables, last_exit_code)

            if not mask_map:
                # Fully unquoted: word removal, then globbing
                if masked == "" and arg != "":
                    continue
                if has_magic(masked):
                    # bash's pathname expansion, segment by segment (see
                    # pathglob); a word that matches nothing stays as typed
                    try:
                        matches = expand_pathname(masked, fs)
                        expanded.extend(matches if matches else [masked])
                    except Exception:
                        expanded.append(masked)
                    continue

            expanded.append(unmask_and_unquote(masked, mask_map))

    return expanded


def _write_to_file(path: str, content: bytes, append: bool, fs: FileSystem):
    """Helper to write/append bytes to file."""
    mode = "a" if append else "w"
    fs.write(path, content, mode=mode)
