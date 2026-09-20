"""Executable form of the FileSystem protocol's documented behavior.

`check_filesystem(fs)` drives every method the protocol declares against a
filesystem you hand it and raises AssertionError on the first one that
misbehaves, naming the method and what was expected. A backend author
outside termish runs it as a plain function call -- no test framework, no
fixtures, nothing to import but termish itself:

    from termish.fs import check_filesystem
    from mypackage import MyFS

    check_filesystem(MyFS())   # raises AssertionError, or returns None

Pass a *fresh, empty* filesystem: the kit creates a scratch directory
under whatever `getcwd()` reports, works inside it, and removes it again
before returning. It never touches anything outside that directory, so a
filesystem whose cwd is not "/" is checked where it stands.

The ranged read gets the most attention here, because the way to get it
wrong is silent: a backend that accepts `offset` and `size` and ignores
them returns the whole file to a caller expecting a slice, and every test
that reads whole files still passes.
"""

from __future__ import annotations

import posixpath
from typing import Any, Callable

__all__ = ["check_filesystem"]

_SCRATCH = ".termish-conformance"


def _fail(method: str, message: str) -> None:
    raise AssertionError(f"{method}: {message}")


def _expect(method: str, what: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        raise AssertionError(f"{method}: {what}; expected {expected!r}, got {actual!r}")


def _expect_raises(
    method: str,
    exc: type[BaseException],
    what: str,
    fn: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> None:
    try:
        fn(*args, **kwargs)
    except exc:
        return
    except Exception as err:
        raise AssertionError(
            f"{method}: {what}; expected {exc.__name__}, "
            f"got {type(err).__name__}: {err}"
        ) from err
    _fail(method, f"{what}; expected {exc.__name__}, but nothing was raised")


def check_filesystem(fs: Any) -> None:
    """Check *fs* against the termish FileSystem protocol.

    Args:
        fs: An empty filesystem instance to exercise. All sixteen protocol
            methods are called, including the ranged form of `read`, append
            mode on `write`, and the `FileInfo.path` convention on
            `list_detailed`.

    Raises:
        AssertionError: On the first deviation, with a message naming the
            method and the expected behavior.
    """
    root = fs.getcwd()
    if not isinstance(root, str) or not root:
        _fail("getcwd", f"expected a non-empty path string, got {root!r}")

    base = posixpath.join(root, _SCRATCH)
    if fs.exists(base):
        _fail(
            "check_filesystem",
            f"expected an empty filesystem, but {base!r} already exists",
        )

    try:
        _check_directories(fs, base)
        _check_write_and_read(fs, base)
        _check_ranged_read(fs, base)
        _check_stat(fs, base)
        _check_listing(fs, base)
        _check_glob(fs, base)
        _check_rename_and_remove(fs, base)
        _check_chdir(fs, root, base)
    finally:
        _cleanup(fs, root, base)


def _check_directories(fs: Any, base: str) -> None:
    fs.mkdir(base)
    if not fs.isdir(base):
        _fail("isdir", f"{base!r} was just created by mkdir, expected True")
    if not fs.exists(base):
        _fail("exists", f"{base!r} was just created by mkdir, expected True")
    if fs.isfile(base):
        _fail("isfile", f"{base!r} is a directory, expected False")

    _expect_raises(
        "mkdir",
        FileExistsError,
        "creating a directory that already exists",
        fs.mkdir,
        base,
    )
    fs.mkdir(base, exist_ok=True)

    deep = posixpath.join(base, "tree/a/b")
    fs.mkdir(deep, parents=True)
    for part in (posixpath.join(base, "tree"), posixpath.join(base, "tree/a"), deep):
        if not fs.isdir(part):
            _fail("mkdir", f"parents=True should have created {part!r}")

    made = posixpath.join(base, "made/x/y")
    fs.makedirs(made)
    if not fs.isdir(made):
        _fail("makedirs", f"expected {made!r} to exist")
    fs.makedirs(made)
    _expect_raises(
        "makedirs",
        FileExistsError,
        "an existing directory with exist_ok=False",
        fs.makedirs,
        made,
        exist_ok=False,
    )

    missing = posixpath.join(base, "nope")
    _expect("exists", f"{missing!r} was never created", fs.exists(missing), False)
    _expect("isfile", f"{missing!r} was never created", fs.isfile(missing), False)
    _expect("isdir", f"{missing!r} was never created", fs.isdir(missing), False)


def _check_write_and_read(fs: Any, base: str) -> None:
    path = posixpath.join(base, "hello.txt")
    fs.write(path, b"hello")
    _expect("isfile", f"{path!r} was just written", fs.isfile(path), True)
    _expect("isdir", f"{path!r} is a file", fs.isdir(path), False)
    _expect("read", "reading back what write stored", fs.read(path), b"hello")

    fs.write(path, b"fresh")
    _expect("write", "mode 'w' replaces the file", fs.read(path), b"fresh")

    fs.write(path, b" bytes", mode="a")
    _expect("write", "mode 'a' appends", fs.read(path), b"fresh bytes")

    new_path = posixpath.join(base, "appended.txt")
    fs.write(new_path, b"start", mode="a")
    _expect(
        "write",
        "mode 'a' on a file that does not exist yet creates it",
        fs.read(new_path),
        b"start",
    )

    _expect_raises(
        "read",
        OSError,
        "reading a path that does not exist",
        fs.read,
        posixpath.join(base, "nope.txt"),
    )


def _check_ranged_read(fs: Any, base: str) -> None:
    path = posixpath.join(base, "ranged.bin")
    data = b"0123456789abcdef"
    end = len(data)
    fs.write(path, data)

    _expect("read", "no range asked for, so the whole file", fs.read(path), data)

    cases = [
        (0, -1, data),
        (0, len(data), data),
        (0, 4, data[:4]),
        (4, -1, data[4:]),
        (4, 4, data[4:8]),
        (end - 1, -1, data[-1:]),
        (0, 0, b""),
        (5, 0, b""),
        (end, -1, b""),
        (end, 4, b""),
        (end + 8, -1, b""),
        (12, 999, data[12:]),
    ]
    for offset, size, expected in cases:
        _expect(
            "read",
            f"offset={offset}, size={size} against a {end}-byte file",
            fs.read(path, offset, size),
            expected,
        )

    _expect(
        "read",
        "offset and size passed by keyword",
        fs.read(path, offset=2, size=3),
        data[2:5],
    )
    _expect_raises("read", ValueError, "a negative offset", fs.read, path, -1, -1)


def _check_stat(fs: Any, base: str) -> None:
    path = posixpath.join(base, "stat.txt")
    fs.write(path, b"12345")

    meta = fs.stat(path)
    _expect("stat", "size of a 5-byte file", meta.size, 5)
    _expect("stat", "is_dir for a file", bool(meta.is_dir), False)
    for field in ("created_at", "modified_at"):
        value = getattr(meta, field)
        if not isinstance(value, str):
            _fail("stat", f"{field} should be an ISO 8601 string, got {value!r}")

    dir_meta = fs.stat(base)
    _expect("stat", "is_dir for a directory", bool(dir_meta.is_dir), True)

    _expect_raises(
        "stat",
        OSError,
        "a path that does not exist",
        fs.stat,
        posixpath.join(base, "nope.txt"),
    )


def _check_listing(fs: Any, base: str) -> None:
    listing = posixpath.join(base, "listing")
    fs.makedirs(posixpath.join(listing, "sub"))
    fs.write(posixpath.join(listing, "one.txt"), b"1")
    fs.write(posixpath.join(listing, "sub/two.txt"), b"22")

    _expect(
        "list",
        f"entries of {listing!r} relative to it",
        sorted(fs.list(listing)),
        ["one.txt", "sub"],
    )
    _expect(
        "list",
        f"recursive entries of {listing!r} relative to it",
        sorted(fs.list(listing, recursive=True)),
        ["one.txt", "sub", "sub/two.txt"],
    )

    infos = {info.path: info for info in fs.list_detailed(listing)}
    _expect(
        "list_detailed",
        "path is the queried directory joined to the relative entry",
        sorted(infos),
        sorted([f"{listing}/one.txt", f"{listing}/sub"]),
    )
    one = infos[f"{listing}/one.txt"]
    _expect("list_detailed", "name is the basename", one.name, "one.txt")
    _expect("list_detailed", "size of a 1-byte file", one.size, 1)
    _expect("list_detailed", "is_dir for a file", bool(one.is_dir), False)
    sub = infos[f"{listing}/sub"]
    _expect("list_detailed", "is_dir for a directory", bool(sub.is_dir), True)
    _expect("list_detailed", "name is the basename", sub.name, "sub")

    deep = {info.path: info for info in fs.list_detailed(listing, recursive=True)}
    _expect(
        "list_detailed",
        "recursive paths keep the queried directory as their prefix",
        sorted(deep),
        sorted([f"{listing}/one.txt", f"{listing}/sub", f"{listing}/sub/two.txt"]),
    )
    nested = deep[f"{listing}/sub/two.txt"]
    _expect(
        "list_detailed",
        "name is the basename, not the relative path",
        nested.name,
        "two.txt",
    )
    _expect("list_detailed", "size of a 2-byte file", nested.size, 2)


def _check_glob(fs: Any, base: str) -> None:
    globbing = posixpath.join(base, "globbing")
    fs.makedirs(globbing)
    for name, content in (("a.txt", b"a"), ("b.txt", b"b"), ("c.md", b"c")):
        fs.write(posixpath.join(globbing, name), content)

    _expect(
        "glob",
        f"{globbing + '/*.txt'!r} should match the two .txt files and nothing else",
        sorted(fs.glob(posixpath.join(globbing, "*.txt"))),
        sorted([f"{globbing}/a.txt", f"{globbing}/b.txt"]),
    )


def _check_rename_and_remove(fs: Any, base: str) -> None:
    moving = posixpath.join(base, "moving")
    fs.makedirs(moving)

    src = posixpath.join(moving, "src.txt")
    dst = posixpath.join(moving, "dst.txt")
    fs.write(src, b"payload")
    fs.rename(src, dst)
    _expect("rename", "the source is gone afterwards", fs.exists(src), False)
    _expect("rename", "contents survive the move", fs.read(dst), b"payload")

    old_dir = posixpath.join(moving, "olddir")
    new_dir = posixpath.join(moving, "newdir")
    fs.makedirs(old_dir)
    fs.write(posixpath.join(old_dir, "child.txt"), b"child")
    fs.rename(old_dir, new_dir)
    _expect("rename", "the source directory is gone", fs.isdir(old_dir), False)
    _expect("rename", "the destination directory exists", fs.isdir(new_dir), True)
    _expect(
        "rename",
        "children move with their directory",
        fs.read(posixpath.join(new_dir, "child.txt")),
        b"child",
    )

    fs.remove(dst)
    _expect("remove", f"{dst!r} was just removed", fs.exists(dst), False)
    _expect_raises(
        "remove", OSError, "removing a path that does not exist", fs.remove, dst
    )

    empty = posixpath.join(moving, "empty")
    fs.mkdir(empty)
    fs.rmdir(empty)
    _expect("rmdir", f"{empty!r} was just removed", fs.isdir(empty), False)
    _expect_raises(
        "rmdir",
        OSError,
        "removing a directory that still has children",
        fs.rmdir,
        new_dir,
    )


def _check_chdir(fs: Any, root: str, base: str) -> None:
    here = posixpath.join(base, "cwd")
    fs.makedirs(here)
    fs.write(posixpath.join(here, "rel.txt"), b"relative")

    fs.chdir(here)
    _expect("getcwd", "the directory chdir was just given", fs.getcwd(), here)
    _expect(
        "read",
        "a relative path resolves against the current directory",
        fs.read("rel.txt"),
        b"relative",
    )
    _expect(
        "read",
        "a ranged read of a relative path",
        fs.read("rel.txt", 0, 3),
        b"rel",
    )
    _expect(
        "list",
        "listing the current directory by default",
        sorted(fs.list()),
        ["rel.txt"],
    )
    fs.write("made-here.txt", b"x")
    _expect(
        "write",
        "a relative path is written under the current directory",
        fs.isfile(posixpath.join(here, "made-here.txt")),
        True,
    )
    _expect(
        "list_detailed",
        "listing the current directory by default",
        sorted(info.name for info in fs.list_detailed()),
        ["made-here.txt", "rel.txt"],
    )

    fs.chdir(root)
    _expect("getcwd", "back at the starting directory", fs.getcwd(), root)
    _expect_raises(
        "chdir",
        OSError,
        "changing to a directory that does not exist",
        fs.chdir,
        posixpath.join(base, "nope"),
    )


def _cleanup(fs: Any, root: str, base: str) -> None:
    """Remove the scratch tree, leaving the filesystem as it was found."""
    try:
        fs.chdir(root)
    except Exception:
        pass
    try:
        if not fs.exists(base):
            return
        entries = [
            posixpath.join(base, entry) for entry in fs.list(base, recursive=True)
        ]
        for path in sorted(entries, key=lambda p: p.count("/"), reverse=True):
            try:
                if fs.isdir(path):
                    fs.rmdir(path)
                elif fs.exists(path):
                    fs.remove(path)
            except Exception:
                pass
        fs.rmdir(base)
    except Exception:
        pass
