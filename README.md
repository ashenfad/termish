# termish 📺

Virtual terminal with shell-like commands over a pluggable filesystem.

Parses and executes shell scripts (pipelines, redirects, semicolons) against any object that implements the `FileSystem` protocol. Zero runtime dependencies. Pure Python.

## Features

- **Shell parser** -- pipes, redirects (`>`, `>>`, `<`), semicolons, quoted strings, line continuation
- **30 builtins** -- ls, cat, grep, find, sed, tr, sort, uniq, cut, wc, diff, tar, gzip, zip, jq, xargs, basename, dirname, ...
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
| Archive | `tar`, `gzip`, `gunzip`, `zip`, `unzip` |
| Meta | `xargs` |
| JSON | `jq` |

## Development

```bash
uv sync --extra dev
uv run pytest
```
