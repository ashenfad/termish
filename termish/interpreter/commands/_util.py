"""Shared utilities for terminal commands."""

from termish.fs import FileSystem


def resolve_path(path: str, fs: FileSystem) -> str:
    """Resolve a relative path against the filesystem's CWD."""
    if path.startswith("/"):
        return path
    cwd = fs.getcwd()
    if cwd == "/":
        return f"/{path}"
    return f"{cwd}/{path}"
