"""Shared utilities for terminal commands."""

import posixpath

from termish.fs import FileSystem


def resolve_path(path: str, fs: FileSystem) -> str:
    """Resolve a relative path against the filesystem's CWD and normalize it."""
    if not path.startswith("/"):
        path = posixpath.join(fs.getcwd(), path)
    return posixpath.normpath(path)
