"""In-memory filesystem implementation.

Provides a simple, self-contained filesystem for testing and lightweight use.
All data lives in Python dicts — nothing touches disk.
"""

import errno as _errno
import fnmatch
import posixpath
from datetime import datetime, timezone

from .protocol import FileInfo, FileMetadata


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryFS:
    """In-memory filesystem satisfying the faketerm FileSystem protocol.

    Files are stored as bytes. Directories are tracked explicitly.
    Timestamps are recorded for created_at and modified_at.
    """

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}
        self._dirs: set[str] = {"/"}
        self._cwd: str = "/"
        # path -> (created_at, modified_at)
        self._timestamps: dict[str, tuple[str, str]] = {}

    # -- helpers --

    def _resolve(self, path: str) -> str:
        """Resolve a path relative to cwd and normalize."""
        if not path.startswith("/"):
            path = self._cwd.rstrip("/") + "/" + path
        return posixpath.normpath(path)

    def _ensure_parent(self, path: str) -> None:
        """Raise if the parent directory of *path* does not exist."""
        parent = posixpath.dirname(path)
        if parent != path and parent not in self._dirs:
            raise FileNotFoundError(_errno.ENOENT, "No such directory", parent)

    def _touch_ts(self, path: str) -> None:
        """Update modified_at; set created_at if new."""
        now = _now_iso()
        if path in self._timestamps:
            self._timestamps[path] = (self._timestamps[path][0], now)
        else:
            self._timestamps[path] = (now, now)

    # -- navigation --

    def getcwd(self) -> str:
        return self._cwd

    def chdir(self, path: str) -> None:
        path = self._resolve(path)
        if path not in self._dirs:
            raise FileNotFoundError(_errno.ENOENT, "No such directory", path)
        self._cwd = path

    # -- read / write --

    def read(self, path: str) -> bytes:
        path = self._resolve(path)
        if path not in self._files:
            if path in self._dirs:
                raise IsADirectoryError(_errno.EISDIR, "Is a directory", path)
            raise FileNotFoundError(_errno.ENOENT, "No such file", path)
        return self._files[path]

    def write(self, path: str, content: bytes, mode: str = "w") -> None:
        path = self._resolve(path)
        parent = posixpath.dirname(path)
        if parent != path and parent not in self._dirs:
            self.makedirs(parent, exist_ok=True)
        if mode == "a" and path in self._files:
            self._files[path] = self._files[path] + content
        else:
            self._files[path] = content
        self._touch_ts(path)

    # -- queries --

    def exists(self, path: str) -> bool:
        path = self._resolve(path)
        return path in self._files or path in self._dirs

    def isfile(self, path: str) -> bool:
        return self._resolve(path) in self._files

    def isdir(self, path: str) -> bool:
        return self._resolve(path) in self._dirs

    def stat(self, path: str) -> FileMetadata:
        path = self._resolve(path)
        if path in self._files:
            ts = self._timestamps.get(path, ("", ""))
            return FileMetadata(
                size=len(self._files[path]),
                created_at=ts[0],
                modified_at=ts[1],
                is_dir=False,
            )
        if path in self._dirs:
            ts = self._timestamps.get(path, ("", ""))
            return FileMetadata(
                size=0,
                created_at=ts[0],
                modified_at=ts[1],
                is_dir=True,
            )
        raise FileNotFoundError(_errno.ENOENT, "No such file or directory", path)

    # -- directory operations --

    def mkdir(self, path: str, exist_ok: bool = False) -> None:
        path = self._resolve(path)
        if path in self._dirs:
            if exist_ok:
                return
            raise FileExistsError(f"Directory exists: '{path}'")
        if path in self._files:
            raise FileExistsError(f"Path exists as file: '{path}'")
        self._ensure_parent(path)
        self._dirs.add(path)
        self._touch_ts(path)

    def makedirs(self, path: str, exist_ok: bool = True) -> None:
        path = self._resolve(path)
        parts = path.strip("/").split("/")
        current = ""
        for part in parts:
            current += "/" + part
            if current not in self._dirs:
                self._dirs.add(current)
                self._touch_ts(current)
            # exist_ok is always True for intermediate dirs

    def rmdir(self, path: str) -> None:
        path = self._resolve(path)
        if path not in self._dirs:
            raise FileNotFoundError(_errno.ENOENT, "No such directory", path)
        if path == "/":
            raise OSError(_errno.EBUSY, "Cannot remove root directory")
        # Check if directory is empty
        prefix = path.rstrip("/") + "/"
        for f in self._files:
            if f.startswith(prefix):
                raise OSError(_errno.ENOTEMPTY, "Directory not empty", path)
        for d in self._dirs:
            if d.startswith(prefix):
                raise OSError(_errno.ENOTEMPTY, "Directory not empty", path)
        self._dirs.discard(path)
        self._timestamps.pop(path, None)

    def remove(self, path: str) -> None:
        path = self._resolve(path)
        if path in self._dirs:
            raise IsADirectoryError(_errno.EISDIR, "Is a directory", path)
        if path not in self._files:
            raise FileNotFoundError(_errno.ENOENT, "No such file", path)
        del self._files[path]
        self._timestamps.pop(path, None)

    def rename(self, src: str, dst: str) -> None:
        src = self._resolve(src)
        dst = self._resolve(dst)
        if src in self._files:
            self._ensure_parent(dst)
            self._files[dst] = self._files.pop(src)
            ts = self._timestamps.pop(src, None)
            if ts:
                self._timestamps[dst] = ts
        elif src in self._dirs:
            src_prefix = src.rstrip("/") + "/"
            self._dirs.discard(src)
            self._dirs.add(dst)
            # Move timestamp
            ts = self._timestamps.pop(src, None)
            if ts:
                self._timestamps[dst] = ts
            # Move children
            for d in list(self._dirs):
                if d.startswith(src_prefix):
                    new_d = dst + d[len(src):]
                    self._dirs.discard(d)
                    self._dirs.add(new_d)
                    ts = self._timestamps.pop(d, None)
                    if ts:
                        self._timestamps[new_d] = ts
            for f in list(self._files):
                if f.startswith(src_prefix):
                    new_f = dst + f[len(src):]
                    self._files[new_f] = self._files.pop(f)
                    ts = self._timestamps.pop(f, None)
                    if ts:
                        self._timestamps[new_f] = ts
        else:
            raise FileNotFoundError(_errno.ENOENT, "No such file or directory", src)

    # -- listing --

    def listdir(self, path: str = "/", recursive: bool = False) -> list[str]:
        path = self._resolve(path)
        if path not in self._dirs:
            raise FileNotFoundError(_errno.ENOENT, "No such directory", path)
        prefix = path.rstrip("/") + "/"

        if recursive:
            results: list[str] = []
            for f in sorted(self._files):
                if f.startswith(prefix):
                    results.append(f[len(prefix):])
            for d in sorted(self._dirs):
                if d.startswith(prefix) and d != path:
                    results.append(d[len(prefix):])
            return sorted(results)

        entries: set[str] = set()
        for f in self._files:
            if f.startswith(prefix):
                rest = f[len(prefix):]
                entries.add(rest.split("/")[0])
        for d in self._dirs:
            if d.startswith(prefix) and d != path:
                rest = d[len(prefix):]
                if rest:
                    entries.add(rest.split("/")[0])
        return sorted(entries)

    def listdir_detailed(
        self, path: str = "/", recursive: bool = False
    ) -> list[FileInfo]:
        path = self._resolve(path)
        if path not in self._dirs:
            raise FileNotFoundError(_errno.ENOENT, "No such directory", path)
        prefix = path.rstrip("/") + "/"

        if recursive:
            results: list[FileInfo] = []
            seen: set[str] = set()
            for f in sorted(self._files):
                if f.startswith(prefix):
                    ts = self._timestamps.get(f, ("", ""))
                    results.append(FileInfo(
                        name=posixpath.basename(f),
                        path=f,
                        size=len(self._files[f]),
                        created_at=ts[0],
                        modified_at=ts[1],
                        is_dir=False,
                    ))
                    seen.add(f)
            for d in sorted(self._dirs):
                if d.startswith(prefix) and d != path and d not in seen:
                    ts = self._timestamps.get(d, ("", ""))
                    results.append(FileInfo(
                        name=posixpath.basename(d),
                        path=d,
                        size=0,
                        created_at=ts[0],
                        modified_at=ts[1],
                        is_dir=True,
                    ))
            return results

        # Non-recursive: direct children only
        entries: dict[str, FileInfo] = {}
        for f in self._files:
            if f.startswith(prefix):
                rest = f[len(prefix):]
                child_name = rest.split("/")[0]
                if "/" in rest:
                    # Implicit subdirectory
                    if child_name not in entries:
                        child_path = prefix + child_name
                        ts = self._timestamps.get(child_path, ("", ""))
                        entries[child_name] = FileInfo(
                            name=child_name,
                            path=child_path,
                            size=0,
                            created_at=ts[0],
                            modified_at=ts[1],
                            is_dir=True,
                        )
                else:
                    ts = self._timestamps.get(f, ("", ""))
                    entries[child_name] = FileInfo(
                        name=child_name,
                        path=f,
                        size=len(self._files[f]),
                        created_at=ts[0],
                        modified_at=ts[1],
                        is_dir=False,
                    )
        for d in self._dirs:
            if d.startswith(prefix) and d != path:
                rest = d[len(prefix):]
                child_name = rest.split("/")[0]
                if child_name not in entries:
                    child_path = prefix + child_name
                    ts = self._timestamps.get(child_path, ("", ""))
                    entries[child_name] = FileInfo(
                        name=child_name,
                        path=child_path,
                        size=0,
                        created_at=ts[0],
                        modified_at=ts[1],
                        is_dir=True,
                    )
        return sorted(entries.values(), key=lambda fi: fi.name)

    # -- globbing --

    def glob(self, pattern: str) -> list[str]:
        if not pattern.startswith("/"):
            pattern = self._cwd.rstrip("/") + "/" + pattern

        # Handle ** recursive globbing: convert ** to regex-compatible form
        if "**" in pattern:
            return self._glob_recursive(pattern)

        pattern = posixpath.normpath(pattern)
        results: list[str] = []
        for f in sorted(self._files):
            if fnmatch.fnmatch(f, pattern):
                results.append(f)
        for d in sorted(self._dirs):
            if d != "/" and fnmatch.fnmatch(d, pattern):
                results.append(d)
        return sorted(set(results))

    def _glob_recursive(self, pattern: str) -> list[str]:
        """Handle glob patterns with ** for recursive matching."""
        import re

        # Convert glob pattern with ** to regex.
        # ** matches zero or more path segments.
        # Strategy: replace /**/ with a regex that matches / or /anything/,
        # then convert remaining * and ? to their glob equivalents.
        regex = re.escape(pattern)
        # Restore glob wildcards that were escaped
        # First handle **: replace \*\* with a sentinel
        regex = regex.replace(r"\*\*", "<<GLOBSTAR>>")
        # Then handle single *
        regex = regex.replace(r"\*", "[^/]*")
        # Handle ?
        regex = regex.replace(r"\?", "[^/]")
        # Now replace globstar: /**/ -> match / or /.../ (zero or more segments)
        regex = regex.replace("/<<GLOBSTAR>>/", "(?:/|/.*/)")
        # Handle **/ at start or ** at end
        regex = regex.replace("<<GLOBSTAR>>/", "(?:.*/)?")
        regex = regex.replace("/<<GLOBSTAR>>", "(?:/.*)?")
        regex = regex.replace("<<GLOBSTAR>>", ".*")
        regex = "^" + regex + "$"
        compiled = re.compile(regex)

        results: list[str] = []
        for f in sorted(self._files):
            if compiled.match(f):
                results.append(f)
        for d in sorted(self._dirs):
            if d != "/" and compiled.match(d):
                results.append(d)
        return sorted(set(results))
