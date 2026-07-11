# termish 📺

Virtual terminal with shell-like commands over a pluggable filesystem.

Parses and executes shell scripts (pipelines, redirects, semicolons) against any object that implements the `FileSystem` protocol. Zero runtime dependencies. Pure Python.

## Features

- **Shell parser** -- pipes, redirects (`>`, `>>`, `<`, `2>`, `2>>`, `2>&1`), heredocs (`<<EOF`), semicolons, quoted strings, line continuation
- **Variable expansion** -- `$?` (last exit code), `$VAR` / `${VAR}` from an env dict; expands in unquoted and double-quoted contexts, literal in single quotes
- **Terminal-faithful transcript** -- stderr diagnostics appear in the returned output when execution continues past a failure (`cmd; next`, `cmd || rescue`), like a real terminal screen; a failure with nothing after it raises `TerminalError`. Stderr redirects are honored: `2>file` captures, `2>/dev/null` suppresses, `2>&1` merges into the pipe (`cmd 2>&1 | head` works)
- **36 builtins** -- ls, cat, grep, find, sed, tr, sort, uniq, cut, wc, diff, tar, gzip, zcat, zip, jq, xargs, file, true, false, basename, dirname, ...
- **Custom commands** -- inject your own command handlers alongside builtins; injected commands override builtins and compose in pipelines
- **jq engine** -- built-in jq filter parser and evaluator (field access, pipes, functions, conditionals)
- **Pluggable filesystem** -- `FileSystem` is a `typing.Protocol`; any object with the right methods works
- **MemoryFS included** -- in-memory filesystem for testing and lightweight use

## Install

```bash
pip install termish
```

## Quick example

```python
from termish import execute, MemoryFS

fs = MemoryFS()

execute("mkdir -p src", fs)
execute("echo 'def main(): pass' > src/app.py", fs)
execute("echo 'import os' > src/utils.py", fs)

# Pipelines work
output = execute("grep -r 'def' src | wc -l", fs)
print(output)  # 1

# jq works
execute('echo \'{"name": "alice", "score": 42}\' > data.json', fs)
output = execute('jq -r ".name" data.json', fs)
print(output)  # alice
```

## Variables

`$?` expands to the last pipeline's exit code. `$VAR` / `${VAR}` read from
an optional env dict, which is shared with command handlers via `ctx.env`
-- mutations persist across commands (and across `execute()` calls if you
reuse the dict):

```python
output = execute('cat /missing; echo "exit=$?"', fs)
print(output)  # exit=1

env = {"NAME": "alice"}
output = execute("echo hello $NAME", fs, env=env)
print(output)  # hello alice
```

Unset variables expand to the empty string. Single quotes suppress
expansion (`'$?'` stays literal). Command substitution `$(...)` is not
supported and raises `ParseError` rather than mangling silently.
Heredoc bodies are never expanded.

## Custom commands

Inject your own commands via the `commands` parameter. They receive a `CommandContext` and compose naturally with builtins in pipelines:

```python
from termish import execute, MemoryFS, CommandContext, CommandResult

def greet(ctx: CommandContext) -> CommandResult | None:
    name = ctx.args[0] if ctx.args else "world"
    ctx.stdout.write(f"hello {name}\n")
    return None

fs = MemoryFS()
output = execute("greet alice | wc -c", fs, commands={"greet": greet})
print(output)  # 12

# Injected commands override builtins with the same name
```

All commands — builtin and injected — use the same `CommandContext` signature. See `CommandContext`, `CommandResult`, and `CommandFunc` in `termish.context` and `termish.errors`.

## FileSystem protocol

Any object implementing these 16 methods works with termish -- no inheritance required:

```python
class FileSystem(Protocol):
    def getcwd(self) -> str: ...
    def chdir(self, path: str) -> None: ...
    def read(self, path: str) -> bytes: ...
    def write(self, path: str, content: bytes, mode: str = "w") -> None: ...
    def exists(self, path: str) -> bool: ...
    def isfile(self, path: str) -> bool: ...
    def isdir(self, path: str) -> bool: ...
    def stat(self, path: str) -> FileMetadata: ...
    def mkdir(self, path: str, parents: bool = False, exist_ok: bool = False) -> None: ...
    def makedirs(self, path: str, exist_ok: bool = True) -> None: ...
    def remove(self, path: str) -> None: ...
    def rmdir(self, path: str) -> None: ...
    def rename(self, src: str, dst: str) -> None: ...
    def list(self, path: str = ".", recursive: bool = False) -> list[str]: ...
    def list_detailed(self, path: str = ".", recursive: bool = False) -> list[FileInfo]: ...
    def glob(self, pattern: str) -> list[str]: ...
```

## Part of the agex stack

termish provides shell commands for AI agents in [agex](https://github.com/ashenfad/agex), operating over virtual filesystems from [monkeyfs](https://github.com/ashenfad/monkeyfs).

## Compatible filesystems

[monkeyfs](https://github.com/ashenfad/monkeyfs) `VirtualFS` and `IsolatedFS` both satisfy the termish `FileSystem` protocol and can be passed directly to `execute()`.

## Builtin commands

| Category | Commands |
|----------|----------|
| Filesystem | `pwd`, `cd`, `mkdir`, `ls`, `touch`, `cp`, `mv`, `rm`, `basename`, `dirname` |
| I/O | `echo`, `cat`, `head`, `tail`, `tee` |
| Search | `grep`, `find` |
| Text | `wc`, `sort`, `uniq`, `cut`, `sed`, `tr` |
| Diff | `diff` |
| Archive | `tar`, `gzip`, `gunzip`, `zcat`/`gzcat`, `zip`, `unzip` |
| Meta | `xargs` |
| JSON | `jq` |
| Inspection | `file` |
| Control | `true`, `false` |

## Development

```bash
uv sync --extra dev
uv run pytest
```
