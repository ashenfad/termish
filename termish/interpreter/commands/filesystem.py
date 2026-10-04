"""
Filesystem commands for the terminal interpreter.
"""

import posixpath
from typing import Any

from termish.context import CommandContext, CommandResult
from termish.errors import TerminalError
from termish.fs import FileSystem

from ._argparse import CommandArgParser
from ._util import resolve_path


def pwd(ctx: CommandContext) -> CommandResult | None:
    """Print working directory."""
    _args, _stdin, stdout, fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    stdout.write(fs.getcwd() + "\n")


def cd(ctx: CommandContext) -> CommandResult | None:
    """Change directory."""
    args, _stdin, _stdout, fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    if not args:
        path = "/"
    else:
        path = args[0]

    try:
        fs.chdir(path)
    except FileNotFoundError:
        raise TerminalError(f"cd: no such file or directory: {path}")
    except NotADirectoryError:
        raise TerminalError(f"cd: not a directory: {path}")
    except Exception as e:
        raise TerminalError(f"cd: {e}")


def mkdir(ctx: CommandContext) -> CommandResult | None:
    """Make directories."""
    args, _stdin, _stdout, fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    parser = CommandArgParser(prog="mkdir", add_help=False)
    parser.add_argument("-p", "--parents", action="store_true")
    parser.add_argument("paths", nargs="+")

    parsed, unknown = parser.parse_known_args(args)
    if unknown:
        raise TerminalError(f"mkdir: unknown arguments: {unknown}")

    for path in parsed.paths:
        try:
            if parsed.parents:
                fs.makedirs(path, exist_ok=True)
            else:
                fs.mkdir(path, exist_ok=False)
        except FileExistsError:
            raise TerminalError(f"mkdir: cannot create directory '{path}': File exists")
        except Exception as e:
            raise TerminalError(f"mkdir: cannot create directory '{path}': {e}")


def _human_readable_size(size: int) -> str:
    """Format size in human-readable units."""
    fsize = float(size)
    for unit in ("B", "K", "M", "G", "T"):
        if abs(fsize) < 1024:
            if unit == "B":
                return f"{int(fsize)}{unit}"
            return f"{fsize:.1f}{unit}"
        fsize /= 1024
    return f"{fsize:.1f}P"


def ls(ctx: CommandContext) -> CommandResult | None:
    """List directory contents."""
    args, _stdin, stdout, fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    parser = CommandArgParser(prog="ls", add_help=False)
    parser.add_argument("-l", action="store_true")
    parser.add_argument("-a", action="store_true")
    parser.add_argument("-R", action="store_true")
    parser.add_argument("-h", "--human-readable", action="store_true")
    parser.add_argument("-t", action="store_true")
    parser.add_argument("-S", action="store_true")
    parser.add_argument("-r", action="store_true")
    parser.add_argument("-d", "--directory", action="store_true")
    parser.add_argument("-F", "--classify", action="store_true")
    parser.add_argument("-1", dest="one_per_line", action="store_true")
    parser.add_argument("paths", nargs="*", default=["."])

    parsed, unknown = parser.parse_known_args(args)
    if unknown:
        raise TerminalError(f"ls: unknown option: {unknown[0]}")

    def long_line(path: str, type_char: str) -> str:
        meta = fs.stat(path)
        if parsed.human_readable:
            size = _human_readable_size(meta.size).rjust(6)
        else:
            size = str(meta.size).rjust(8)
        time = meta.modified_at[:16].replace("T", " ") if meta.modified_at else " " * 16
        return f"{type_char}rw-r--r-- 1 agent agent {size} {time} {path}\n"

    def list_dir(path: str) -> None:
        needs_detailed = parsed.l or parsed.t or parsed.S or parsed.classify
        if needs_detailed:
            items = fs.list_detailed(path, recursive=parsed.R)
            if not parsed.a:
                items = [i for i in items if not i.name.startswith(".")]
            if parsed.S:
                items = sorted(items, key=lambda x: x.size, reverse=True)
            elif parsed.t:
                items = sorted(items, key=lambda x: x.modified_at or "", reverse=True)
            if parsed.r:
                items = list(reversed(items))

            if parsed.l:
                for item in items:
                    type_char = "d" if item.is_dir else "-"
                    if parsed.human_readable:
                        size = _human_readable_size(item.size).rjust(6)
                    else:
                        size = str(item.size).rjust(8)
                    time = (
                        item.modified_at[:16].replace("T", " ")
                        if item.modified_at
                        else " " * 16
                    )
                    suffix = "/" if parsed.classify and item.is_dir else ""
                    stdout.write(
                        f"{type_char}rw-r--r-- 1 agent agent {size} {time} {item.path}{suffix}\n"
                    )
            else:
                filtered = [
                    item.path + ("/" if parsed.classify and item.is_dir else "")
                    for item in items
                ]
                if filtered:
                    stdout.write("\n".join(filtered) + "\n")
        else:
            items_str = fs.list(path, recursive=parsed.R)
            filtered = [
                p for p in items_str if parsed.a or not p.split("/")[-1].startswith(".")
            ]
            if parsed.r:
                filtered = list(reversed(filtered))
            if filtered:
                stdout.write("\n".join(filtered) + "\n")

    # GNU's order: an operand that is missing is reported and the rest
    # are listed; file operands (and, with -d, directories as entries)
    # come first, by name and with no header; then each directory, by
    # name, under a "dir:" header whenever there is more than one
    # operand, with a blank line before every group after the first.
    missing: list[str] = []
    files: list[str] = []
    dirs: list[str] = []
    for path in parsed.paths:
        try:
            if fs.isdir(path):
                (files if parsed.directory else dirs).append(path)
            elif fs.isfile(path) or fs.exists(path):
                files.append(path)
            else:
                missing.append(path)
        except Exception:
            missing.append(path)

    def operand_order(paths: list[str]) -> list[str]:
        """Operands in the order their entries would be: by name, or by
        size or time (largest and newest first) under -S and -t, and
        reversed under -r."""
        if parsed.S or parsed.t:

            def key(p: str) -> Any:
                try:
                    meta = fs.stat(p)
                except Exception:
                    return 0 if parsed.S else ""
                return meta.size if parsed.S else (meta.modified_at or "")

            paths = sorted(sorted(paths), key=key, reverse=True)
        else:
            paths = sorted(paths)
        return list(reversed(paths)) if parsed.r else paths

    files = operand_order(files)
    dirs = operand_order(dirs)
    errors = [f"ls: cannot access '{p}': No such file or directory" for p in missing]
    if not files and not dirs:
        raise TerminalError("\n".join(errors))

    try:
        for path in files:
            if parsed.l:
                stdout.write(long_line(path, "d" if fs.isdir(path) else "-"))
            else:
                stdout.write(f"{path}\n")
        headers = len(parsed.paths) > 1
        for k, path in enumerate(dirs):
            if files or k:
                stdout.write("\n")
            if headers:
                stdout.write(f"{path}:\n")
            list_dir(path)
    except FileNotFoundError as e:
        raise TerminalError(
            f"ls: cannot access '{e.filename or e}': No such file or directory"
        )
    except NotADirectoryError as e:
        raise TerminalError(f"ls: cannot access '{e.filename or e}': Not a directory")
    except TerminalError:
        raise
    except Exception as e:
        raise TerminalError(f"ls: {e}")

    if errors:
        return CommandResult(exit_code=2, stderr="\n".join(errors) + "\n")
    return None


def touch(ctx: CommandContext) -> CommandResult | None:
    """Update timestamps or create empty files."""
    args, _stdin, _stdout, fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    parser = CommandArgParser(prog="touch", add_help=False)
    parser.add_argument("-c", "--no-create", action="store_true")
    parser.add_argument("files", nargs="*")

    parsed, unknown = parser.parse_known_args(args)
    if unknown:
        raise TerminalError(f"touch: unknown option: {unknown[0]}")

    if not parsed.files:
        raise TerminalError("touch: missing file operand")

    for path in parsed.files:
        try:
            if not fs.exists(path):
                if not parsed.no_create:
                    fs.write(path, b"")
            else:
                content = fs.read(path)
                fs.write(path, content)
        except Exception as e:
            raise TerminalError(f"touch: {e}")


def _copy_recursive(src: str, dst: str, fs: FileSystem) -> None:
    """Recursively copy src directory to dst."""
    if not fs.exists(dst):
        fs.mkdir(dst)

    for item in fs.list_detailed(src, recursive=False):
        name = item.path.rstrip("/").split("/")[-1]
        src_path = f"{src.rstrip('/')}/{name}"
        dst_path = f"{dst.rstrip('/')}/{name}"

        if item.is_dir:
            _copy_recursive(src_path, dst_path, fs)
        else:
            content = fs.read(src_path)
            fs.write(dst_path, content)


def cp(ctx: CommandContext) -> CommandResult | None:
    """Copy files."""
    args, _stdin, _stdout, fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    parser = CommandArgParser(prog="cp", add_help=False)
    parser.add_argument("-r", "-R", action="store_true")
    parser.add_argument("-a", "--archive", action="store_true")
    parser.add_argument("src", nargs="+")
    parser.add_argument("dst")

    parsed, unknown = parser.parse_known_args(args)
    if unknown:
        raise TerminalError(f"cp: unknown option: {unknown[0]}")

    if parsed.archive:
        parsed.r = True

    if len(parsed.src) > 1:
        sources = parsed.src
        dst = parsed.dst
        if not fs.isdir(dst):
            raise TerminalError(f"cp: target '{dst}' is not a directory")
    else:
        sources = [parsed.src[0]]
        dst = parsed.dst

    for src in sources:
        try:
            if fs.isdir(src):
                if not parsed.r:
                    raise TerminalError(
                        f"cp: -r not specified; omitting directory '{src}'"
                    )

                # Determine target path
                if fs.isdir(dst):
                    dirname = posixpath.basename(src.rstrip("/"))
                    target_path = f"{dst.rstrip('/')}/{dirname}"
                else:
                    target_path = dst

                # Check for copying into self
                src_abs = resolve_path(src, fs)
                dst_abs = resolve_path(target_path, fs)
                if dst_abs.startswith(src_abs.rstrip("/") + "/"):
                    raise TerminalError(f"cp: cannot copy '{src}' into itself")

                _copy_recursive(src, target_path, fs)
            else:
                content = fs.read(src)

                if fs.isdir(dst):
                    filename = posixpath.basename(src)
                    target_path = f"{dst}/{filename}"
                else:
                    target_path = dst

                fs.write(target_path, content)

        except FileNotFoundError:
            raise TerminalError(f"cp: cannot stat '{src}': No such file or directory")
        except TerminalError:
            raise
        except Exception as e:
            raise TerminalError(f"cp: {e}")


def mv(ctx: CommandContext) -> CommandResult | None:
    """Move files."""
    args, _stdin, _stdout, fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    parser = CommandArgParser(prog="mv", add_help=False)
    parser.add_argument("-f", "--force", action="store_true")
    parser.add_argument("-n", "--no-clobber", action="store_true")
    parser.add_argument("paths", nargs="+")

    parsed, unknown = parser.parse_known_args(args)
    if unknown:
        raise TerminalError(f"mv: unknown option: {unknown[0]}")

    if len(parsed.paths) < 2:
        raise TerminalError("mv: missing destination file operand")
    if len(parsed.paths) > 2:
        raise TerminalError("mv: too many arguments")

    src, dst = parsed.paths
    if parsed.no_clobber and fs.exists(dst):
        return  # silently skip

    try:
        fs.rename(src, dst)
    except FileNotFoundError:
        raise TerminalError(f"mv: cannot stat '{src}': No such file or directory")
    except Exception as e:
        raise TerminalError(f"mv: {e}")


def _remove_recursive(path: str, fs: FileSystem) -> None:
    """Recursively remove directory and contents."""
    for item in fs.list_detailed(path, recursive=False):
        name = item.path.rstrip("/").split("/")[-1]
        item_path = f"{path.rstrip('/')}/{name}"

        if item.is_dir:
            _remove_recursive(item_path, fs)
        else:
            fs.remove(item_path)

    fs.rmdir(path)


def rm(ctx: CommandContext) -> CommandResult | None:
    """Remove files."""
    args, _stdin, _stdout, fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    parser = CommandArgParser(prog="rm", add_help=False)
    parser.add_argument("-r", "-R", action="store_true")
    parser.add_argument("-f", "--force", action="store_true")
    parser.add_argument("paths", nargs="+")

    parsed, unknown = parser.parse_known_args(args)
    if unknown:
        raise TerminalError(f"rm: unknown option: {unknown[0]}")

    for path in parsed.paths:
        try:
            if fs.isdir(path):
                if not parsed.r:
                    raise TerminalError(
                        f"rm: cannot remove '{path}': Is a directory (use -r to remove)"
                    )

                # Prevent removing root
                resolved = resolve_path(path, fs)
                if resolved == "/" or resolved == "":
                    raise TerminalError("rm: cannot remove root directory")

                _remove_recursive(path, fs)
            else:
                fs.remove(path)
        except FileNotFoundError:
            if not parsed.force:
                raise TerminalError(
                    f"rm: cannot remove '{path}': No such file or directory"
                )
        except TerminalError:
            raise
        except Exception as e:
            raise TerminalError(f"rm: {path}: {e}")


def basename(ctx: CommandContext) -> CommandResult | None:
    """Strip directory and suffix from filenames."""
    args, _stdin, stdout, _fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    if not args:
        raise TerminalError("basename: missing operand")
    path = args[0]
    name = path.rstrip("/").rsplit("/", 1)[-1] if "/" in path else path
    if len(args) > 1:
        suffix = args[1]
        if name.endswith(suffix) and name != suffix:
            name = name[: -len(suffix)]
    stdout.write(name + "\n")


def dirname(ctx: CommandContext) -> CommandResult | None:
    """Strip last component from file name."""
    args, _stdin, stdout, _fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    if not args:
        raise TerminalError("dirname: missing operand")
    path = args[0]
    if "/" not in path:
        stdout.write(".\n")
    else:
        parent = path.rstrip("/").rsplit("/", 1)[0]
        stdout.write((parent or "/") + "\n")
