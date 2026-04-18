"""
Core interpreter logic for executing terminal scripts against a FileSystem.
Functional implementation.
"""

import contextvars
import io
from collections.abc import Mapping
from typing import TextIO

from termish.ast import Pipeline, Script
from termish.context import CommandContext
from termish.errors import CommandFunc, TerminalError
from termish.fs import FileSystem
from termish.quote_masker import mask_quotes, unmask_and_unquote

from .commands import archive, filesystem, meta, search, text
from .commands import diff as diff_cmd
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
    "zip": archive.zip_cmd,
    "unzip": archive.unzip,
}


def execute_script(
    script: Script,
    fs: FileSystem,
    commands: Mapping[str, CommandFunc] | None = None,
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

    Returns:
        Captured stdout as a string.

    Raises:
        TerminalError: If the last executed pipeline failed (contains partial output).
    """
    # Only set the context var if commands is explicitly provided.
    # When None, nested calls inherit the parent's injected commands.
    if commands is None:
        return _execute_script_inner(script, fs)

    token = _injected_commands.set(commands)
    try:
        return _execute_script_inner(script, fs)
    finally:
        _injected_commands.reset(token)


def _execute_script_inner(script: Script, fs: FileSystem) -> str:
    """Inner execution loop (injected commands already set via context var)."""
    final_output = io.StringIO()
    last_succeeded = True
    last_error: TerminalError | None = None

    for i, pipeline in enumerate(script.pipelines):
        # Determine whether to execute this pipeline based on the preceding operator
        if i > 0:
            op = script.operators[i - 1]
            if op == "&&" and not last_succeeded:
                continue
            elif op == "||" and last_succeeded:
                continue
            # ";" always executes

        try:
            _execute_pipeline(pipeline, fs, final_output)
            last_succeeded = True
            last_error = None
        except TerminalError as e:
            last_succeeded = False
            last_error = e
        except Exception as e:
            last_succeeded = False
            last_error = TerminalError(f"Unexpected error: {e}")

    if last_error is not None:
        raise TerminalError(last_error.message, partial_output=final_output.getvalue())

    return final_output.getvalue()


def _execute_pipeline(pipeline: Pipeline, fs: FileSystem, stdout: TextIO):
    """
    Execute a chain of commands.
    Raises TerminalError on failure.
    """
    if not pipeline.commands:
        return

    current_input: str | None = None

    for cmd_node in pipeline.commands:
        cmd_stdin = io.StringIO(current_input) if current_input else io.StringIO()
        cmd_stdout = io.StringIO()

        # Handle Redirects (Input)
        input_redirect = next((r for r in cmd_node.redirects if r.type == "<"), None)
        if input_redirect:
            path = resolve_path(input_redirect.target, fs)
            try:
                content_bytes = fs.read(path)
                content_str = content_bytes.decode("utf-8", errors="replace")
                cmd_stdin = io.StringIO(content_str)
            except Exception as e:
                raise TerminalError(f"{cmd_node.name}: {input_redirect.target}: {e}")

        # Prepare Args
        expanded_args = _expand_args(cmd_node.args, fs)

        # Execute Command — injected commands override built-ins
        cmd_func = _resolve_command(cmd_node.name)
        if cmd_func is not None:
            try:
                ctx = CommandContext(
                    args=expanded_args, stdin=cmd_stdin, stdout=cmd_stdout, fs=fs
                )
                result = cmd_func(ctx)
                if result is not None and result.exit_code != 0:
                    raise TerminalError(
                        f"{cmd_node.name}: {result.stderr}"
                        if result.stderr
                        else f"{cmd_node.name}: exited with code {result.exit_code}"
                    )
            except TerminalError:
                raise
            except Exception as e:
                raise TerminalError(f"{cmd_node.name}: execution error: {e}")
        else:
            raise TerminalError(f"{cmd_node.name}: command not found")

        # Capture output
        output_content = cmd_stdout.getvalue()

        # Handle Output Redirects
        output_redirects = [r for r in cmd_node.redirects if r.type in (">", ">>")]

        if output_redirects:
            for r in output_redirects:
                path = resolve_path(r.target, fs)
                try:
                    _write_to_file(path, output_content, r.type == ">>", fs)
                except Exception as e:
                    raise TerminalError(f"{cmd_node.name}: redirect failed: {e}")
            current_input = ""
        else:
            current_input = output_content

    if current_input:
        stdout.write(current_input)


def _expand_args(args: list[str], fs: FileSystem) -> list[str]:
    """Perform globbing on arguments."""
    expanded: list[str] = []
    for arg in args:
        masked, mask_map = mask_quotes(arg)

        if ("*" in masked or "?" in masked) and not mask_map:
            try:
                matches = fs.glob(arg)
                if matches:
                    expanded.extend(matches)
                else:
                    expanded.append(arg)
            except Exception:
                expanded.append(arg)
        else:
            expanded.append(unmask_and_unquote(masked, mask_map))

    return expanded


def _write_to_file(path: str, content: str, append: bool, fs: FileSystem):
    """Helper to write/append text to file."""
    content_bytes = content.encode("utf-8")
    mode = "a" if append else "w"
    fs.write(path, content_bytes, mode=mode)
