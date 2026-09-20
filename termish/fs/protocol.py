"""FileSystem protocol and data types for termish.

Defines the structural interface that any filesystem must satisfy
to work with termish's terminal commands.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class FileMetadata:
    """Metadata for a single file or directory.

    Attributes:
        size: File size in bytes (0 for directories).
        created_at: ISO 8601 timestamp when file was created (UTC).
        modified_at: ISO 8601 timestamp when file was last modified (UTC).
        is_dir: True if this is a directory, False for files.
    """

    size: int
    created_at: str
    modified_at: str
    is_dir: bool = False


@dataclass(frozen=True)
class FileInfo:
    """Complete file information for directory listings.

    Attributes:
        name: File or directory name (basename).
        path: Full path to file or directory.
        size: File size in bytes (0 for directories).
        created_at: ISO 8601 timestamp when created (UTC).
        modified_at: ISO 8601 timestamp when last modified (UTC).
        is_dir: True if this is a directory, False if file.
    """

    name: str
    path: str  # queried_dir/relative — e.g. list_detailed("src/").path → "src/lib/util.py"
    size: int
    created_at: str
    modified_at: str
    is_dir: bool


@runtime_checkable
class FileSystem(Protocol):
    """Structural interface for filesystems used by terminal commands.

    Any object implementing these methods can be passed to termish's
    interpreter. No inheritance required — uses structural (duck) typing.
    """

    def getcwd(self) -> str:
        """Return the current working directory."""
        ...

    def chdir(self, path: str) -> None:
        """Change the current working directory."""
        ...

    def read(self, path: str, offset: int = 0, size: int = -1) -> bytes:
        """Read a file's bytes, or a byte range of them.

        The range is described in file-object terms, so a backend that
        can fetch a range -- a real file, an HTTP endpoint that answers
        206, a block store -- can serve a caller that wants twenty bytes
        of a gigabyte without moving the gigabyte.

        Args:
            path: File path.
            offset: Byte position from the start of the file. Must be
                >= 0; a negative offset raises ValueError.
            size: How many bytes to read. -1 (the default) reads to the
                end of the file; 0 returns b"".

        Returns:
            The requested bytes. With both defaults this is the entire
            file, byte for byte, so callers that ask for no range are
            unaffected by the range existing. A read starting at or past
            the end of the file returns b"". A read that runs past the
            end is truncated to what is there, never an error.

        An implementation that accepts the two arguments and ignores
        them is wrong, not merely slow: it returns the whole file where
        the caller asked for a slice, and a caller that trusts the
        length or the starting position then reads the wrong bytes.
        `termish.fs.check_filesystem` checks for exactly that.
        """
        ...

    def write(self, path: str, content: bytes, mode: str = "w") -> None:
        """Write bytes to a file.

        Args:
            path: File path.
            content: Bytes to write.
            mode: 'w' for overwrite, 'a' for append.
        """
        ...

    def exists(self, path: str) -> bool:
        """Check if a path exists."""
        ...

    def isfile(self, path: str) -> bool:
        """Check if a path is a regular file."""
        ...

    def isdir(self, path: str) -> bool:
        """Check if a path is a directory."""
        ...

    def stat(self, path: str) -> FileMetadata:
        """Return metadata for a path."""
        ...

    def mkdir(self, path: str, parents: bool = False, exist_ok: bool = False) -> None:
        """Create a directory."""
        ...

    def makedirs(self, path: str, exist_ok: bool = True) -> None:
        """Create a directory tree."""
        ...

    def remove(self, path: str) -> None:
        """Remove a file."""
        ...

    def rmdir(self, path: str) -> None:
        """Remove an empty directory."""
        ...

    def rename(self, src: str, dst: str) -> None:
        """Rename or move a file or directory."""
        ...

    def list(self, path: str = ".", recursive: bool = False) -> list[str]:
        """List directory contents as paths.

        Args:
            path: Directory to list.
            recursive: If True, list all descendants.
        """
        ...

    def list_detailed(self, path: str = ".", recursive: bool = False) -> list[FileInfo]:
        """List directory contents with full metadata.

        Args:
            path: Directory to list.
            recursive: If True, list all descendants.
        """
        ...

    def glob(self, pattern: str) -> list[str]:
        """Return paths matching a glob pattern."""
        ...
