"""
Core interpreter logic for executing terminal scripts against a FileSystem.
Functional implementation.
"""

import contextvars
import io
import re
from collections.abc import Mapping
from typing import TextIO

from termish.ast import Pipeline, Script
from termish.context import CommandContext
from termish.errors import CommandFunc, TerminalError
from termish.fs import FileSystem
from termish.quote_masker import mask_quotes, unmask_and_unquote

from .commands import archive, control, filesystem, meta, search, text
from .commands import diff as diff_cmd
from .commands import file as file_mod
from .commands import io as io_cmds
from .commands import jq as jq_cmd
from .commands import sed as sed_cmd
from .commands._util import resolve_path

# Context var holding injected commands for the current execution.
# Set by execute_script() so that meta-commands like xargs can resolve
# injected commands without threading a parameter through every call.
_injected_commands: contextvars.ContextVar[Mapping[str, CommandFunc]] = (
    contextvars.ContextVar("_injected_commands", default={})
)


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
    # Control
    "true": control.true_cmd,
    "false": control.false_cmd,
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
            the caller. Defaults to an empty environment.

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
    final_output = io.StringIO()
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
            final_output.write(pending_diag)
        pending_diag = ""

        try:
            _execute_pipeline(pipeline, fs, final_output, env, last_exit_code)
            last_succeeded = True
            last_error = None
            last_exit_code = 0
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
            partial_output=final_output.getvalue(),
            exit_code=last_error.exit_code,
            stderr=last_error.stderr,
        )

    return final_output.getvalue()


def _execute_pipeline(
    pipeline: Pipeline,
    fs: FileSystem,
    stdout: TextIO,
    env: dict[str, str],
    last_exit_code: int,
):
    """
    Execute a chain of commands.
    Raises TerminalError on failure.
    """
    if not pipeline.commands:
        return

    current_input: str | None = None
    merged_failure: TerminalError | None = None

    for cmd_node in pipeline.commands:
        cmd_stdin = io.StringIO(current_input) if current_input else io.StringIO()
        cmd_stdout = io.StringIO()

        # Expand the command name and args as one word list so an
        # empty-expanding name shifts away (zsh-style: `$UNSET echo hi`
        # runs `echo hi`).  Multi-word values do NOT field-split —
        # deliberately zsh, not bash: `CMD="echo hello"; $CMD` is
        # "echo hello: command not found", never a silent re-parse.
        words = _expand_args([cmd_node.name] + cmd_node.args, fs, env, last_exit_code)
        if not words:
            # The whole command expanded to nothing (`$UNSET` alone):
            # a silent no-op, like zsh.  Redirects are not processed.
            merged_failure = None
            current_input = ""
            continue
        cmd_name, expanded_args = words[0], words[1:]

        # Handle Redirects (Input) — last input-ish redirect wins (bash)
        input_redirect = next(
            (r for r in reversed(cmd_node.redirects) if r.type in ("<", "<<")),
            None,
        )
        if input_redirect is not None:
            if input_redirect.type == "<<":
                # Heredoc bodies are always literal (as if the delimiter
                # were quoted) — no expansion.
                cmd_stdin = io.StringIO(input_redirect.content or "")
            else:
                target = _expand_word(input_redirect.target, env, last_exit_code)
                path = resolve_path(target, fs)
                try:
                    content_bytes = fs.read(path)
                    content_str = content_bytes.decode("utf-8", errors="replace")
                    cmd_stdin = io.StringIO(content_str)
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
            if result is not None and result.exit_code != 0:
                raise TerminalError(
                    f"{cmd_name}: {result.stderr}"
                    if result.stderr
                    else f"{cmd_name}: exited with code {result.exit_code}",
                    exit_code=result.exit_code,
                    stderr=result.stderr,
                )
        except TerminalError as e:
            if stderr_redirect is None:
                raise
            failure = e
        except Exception as e:
            wrapped = TerminalError(f"{cmd_name}: execution error: {e}")
            if stderr_redirect is None:
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
                stdout.write(err_text)
        elif stderr_redirect.type == "2>&1":
            # Merge into stdout: joins the pipe / transcript below.
            cmd_stdout.write(err_text)
        else:
            # 2>file truncates (even when no stderr was produced, as in
            # bash); 2>>file appends; /dev/null discards.
            target = _expand_word(stderr_redirect.target, env, last_exit_code)
            if target != "/dev/null":
                path = resolve_path(target, fs)
                try:
                    _write_to_file(path, err_text, stderr_redirect.type == "2>>", fs)
                except Exception as e:
                    raise TerminalError(f"{cmd_name}: redirect failed: {e}")

        # A failure whose stderr went to a file still aborts the pipeline
        # (silently — its diagnostic was consumed by the redirect).  A
        # "2>&1" failure keeps the pipeline going so downstream stages see
        # the merged text (the agent idiom ``cmd 2>&1 | head``); if it's
        # the final stage, the failure resurfaces after the loop.
        if failure is not None and stderr_redirect.type != "2>&1":
            raise TerminalError(failure.message, exit_code=failure.exit_code, stderr="")
        merged_failure = failure  # only ever non-None for "2>&1"

        # Capture output
        output_content = cmd_stdout.getvalue()

        # Handle Output Redirects
        output_redirects = [r for r in cmd_node.redirects if r.type in (">", ">>")]

        if output_redirects:
            for r in output_redirects:
                target = _expand_word(r.target, env, last_exit_code)
                path = resolve_path(target, fs)
                try:
                    _write_to_file(path, output_content, r.type == ">>", fs)
                except Exception as e:
                    raise TerminalError(f"{cmd_name}: redirect failed: {e}")
            current_input = ""
        else:
            current_input = output_content

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
# literal — visible non-support beats silent mangling.  The escape
# alternatives implement the double-quote backslash rule: backslash is
# special only before '\' or '$' (so "\d" in a regex stays "\d", while
# "\\$NAME" is an escaped backslash followed by a live expansion).
_VAR_RE = re.compile(
    r"\\\\"  # escaped backslash -> literal backslash
    r"|\\\$"  # escaped dollar -> literal $
    r"|\$\?"  # last exit code
    r"|\$\{([A-Za-z_][A-Za-z0-9_]*)\}"  # ${NAME}
    r"|\$([A-Za-z_][A-Za-z0-9_]*)"  # $NAME
)


def _expand_vars(text: str, env: dict[str, str], last_exit_code: int) -> str:
    """Expand ``$?``, ``$NAME``, and ``${NAME}`` in text.

    Unset variables expand to the empty string (POSIX).  ``\\$``
    suppresses expansion and yields a literal ``$``; ``\\\\`` yields a
    literal backslash (so ``\\\\$NAME`` is a backslash plus expansion).
    """
    if "$" not in text and "\\\\" not in text:
        return text

    def repl(m: re.Match) -> str:
        s = m.group(0)
        if s == "\\\\":
            return "\\"
        if s == "\\$":
            return "$"
        if s == "$?":
            return str(last_exit_code)
        name = m.group(1) or m.group(2)
        return env.get(name, "")

    return _VAR_RE.sub(repl, text)


def _expand_masked(
    masked: str,
    mask_map: dict[str, str],
    env: dict[str, str],
    last_exit_code: int,
) -> str:
    """Variable-expand a quote-masked word, honoring quote semantics.

    The unquoted portion and the contents of double-quoted regions are
    expanded; single-quoted regions stay literal.  Returns the masked
    text (mask_map is updated in place for expanded regions).
    """
    masked = _expand_vars(masked, env, last_exit_code)
    for token, original in mask_map.items():
        if original[0] == '"':
            inner = _expand_vars(original[1:-1], env, last_exit_code)
            mask_map[token] = '"' + inner + '"'
    return masked


def _expand_word(word: str, env: dict[str, str], last_exit_code: int) -> str:
    """Expand variables in a single word (command name or redirect target)
    and strip quotes.  No globbing, no word removal."""
    masked, mask_map = mask_quotes(word)
    masked = _expand_masked(masked, mask_map, env, last_exit_code)
    return unmask_and_unquote(masked, mask_map)


def _expand_args(
    args: list[str],
    fs: FileSystem,
    env: dict[str, str],
    last_exit_code: int,
) -> list[str]:
    """Perform variable expansion and globbing on arguments.

    Variables expand first (so ``$?`` never reads as a glob ``?``), then
    fully-unquoted args containing wildcards are globbed.  An unquoted
    arg that expands to nothing is removed entirely (bash word removal:
    ``echo a $UNSET b`` has two args, not three).
    """
    expanded: list[str] = []
    for arg in args:
        masked, mask_map = mask_quotes(arg)
        masked = _expand_masked(masked, mask_map, env, last_exit_code)

        if not mask_map:
            # Fully unquoted: word removal, then globbing
            if masked == "" and arg != "":
                continue
            if "*" in masked or "?" in masked:
                try:
                    matches = fs.glob(masked)
                    expanded.extend(matches if matches else [masked])
                except Exception:
                    expanded.append(masked)
                continue

        expanded.append(unmask_and_unquote(masked, mask_map))

    return expanded


def _write_to_file(path: str, content: str, append: bool, fs: FileSystem):
    """Helper to write/append text to file."""
    content_bytes = content.encode("utf-8")
    mode = "a" if append else "w"
    fs.write(path, content_bytes, mode=mode)
