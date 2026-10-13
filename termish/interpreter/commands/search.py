"Search commands (grep, find) for the terminal interpreter."

from __future__ import annotations

import fnmatch
import io
import itertools
import re
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

from termish.context import CommandContext, CommandResult
from termish.errors import TerminalError
from termish.fs import FileSystem

from ._argparse import CommandArgParser
from ._dispatch import dispatch_argv, join_stderr, stderr_of
from ._regex import bre_to_python

# How many files grep reads in one batch: enough that a filesystem that
# fetches remotely answers a typical tree in a round trip or two, few
# enough that a large tree is never all in memory at once.
_READ_BATCH = 256


def _collect_files(user_dir: str, out: list[tuple[str, bool]], fs: FileSystem) -> None:
    """Add every entry under *user_dir* to *out*, preserving the user's prefix.

    Each entry is paired with True: it came from a listing, so it may be a
    directory. Telling which took an ``isdir`` call per entry, a round trip
    each on a remote filesystem; ``_read_files`` sorts them out instead.

    Uses ``fs.list()`` (which returns paths relative to the queried directory)
    rather than ``list_detailed()``, whose ``.path`` format varies across FS
    implementations and which can cost a ``stat`` per entry behind a mount.
    """
    prefix = user_dir.rstrip("/")
    try:
        entries = fs.list(user_dir, recursive=True)
    except Exception as e:
        raise TerminalError(f"grep: {user_dir}: {e}")
    out.extend((f"{prefix}/{entry}", True) for entry in entries)


def _read_files(
    fs: FileSystem, targets: list[tuple[str, bool]]
) -> Iterator[tuple[str, bytes | Exception]]:
    """Yield each file in *targets* with its bytes, in order, or the
    exception reading it raised.

    Files are read ``_READ_BATCH`` at a time with ``fs.read_many`` when the
    filesystem has it, so a remote one answers each batch in a single
    round trip; otherwise with one ``read`` per file. A target is a path
    and whether it came from listing a directory. A listed path that is
    not a file is skipped: a directory, which the listing does not mark,
    or a file gone since. A path named on the command line that cannot be
    read is read again on its own, so its error is the one ``read`` gives.
    A batch that fails, or answers with keys it was not asked for, is
    read file by file instead.
    """
    read_many = getattr(fs, "read_many", None)
    for start in range(0, len(targets), _READ_BATCH):
        chunk = targets[start : start + _READ_BATCH]
        found: dict[str, bytes] | None = None
        if read_many is not None:
            asked = [path for path, _ in chunk]
            try:
                found = read_many(asked)
            except Exception:
                found = None  # file by file, so each error names its own path
            if found is not None and not found.keys() <= set(asked):
                # Keys must be the paths as given. One spelled otherwise
                # (made absolute, say) would leave every listed file
                # looking like a directory, so the answer is not used.
                found = None
        for path, from_listing in chunk:
            if found is not None:
                if path in found:
                    yield path, found[path]
                    continue
                if from_listing:
                    continue  # read_many leaves out what is not a file
            try:
                content = fs.read(path)
            except Exception as e:
                # A directory read may raise anything (FileNotFoundError on
                # some filesystems), so only a failed read asks what it was.
                if from_listing and _isdir(fs, path):
                    continue
                yield path, e
            else:
                yield path, content


def _isdir(fs: FileSystem, path: str) -> bool:
    try:
        return fs.isdir(path)
    except Exception:
        return False


def grep(ctx: CommandContext) -> CommandResult | None:
    """Print lines that match patterns."""
    args, stdin, stdout, fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    parser = CommandArgParser(prog="grep", add_help=False)
    parser.add_argument("-i", "--ignore-case", action="store_true")
    parser.add_argument("-n", "--line-number", action="store_true")
    parser.add_argument("-r", "-R", "--recursive", action="store_true")
    parser.add_argument("-l", "--files-with-matches", action="store_true")
    parser.add_argument("-L", "--files-without-match", action="store_true")
    parser.add_argument("-v", "--invert-match", action="store_true")
    parser.add_argument("-F", "--fixed-strings", action="store_true")
    parser.add_argument("-E", "--extended-regexp", action="store_true")
    parser.add_argument("-A", "--after-context", type=int, default=0)
    parser.add_argument("-B", "--before-context", type=int, default=0)
    parser.add_argument("-C", "--context", type=int, default=0)
    parser.add_argument("-c", "--count", action="store_true")
    parser.add_argument("-w", "--word-regexp", action="store_true")
    parser.add_argument("-o", "--only-matching", action="store_true")
    parser.add_argument("-q", "--quiet", "--silent", action="store_true")
    parser.add_argument("-m", "--max-count", type=int, default=0)
    parser.add_argument("--include", type=str, default=None)
    parser.add_argument("--exclude", type=str, default=None)
    parser.add_argument("--exclude-dir", type=str, default=None)
    parser.add_argument("-H", "--with-filename", action="store_true")
    parser.add_argument("-h", "--no-filename", action="store_true", dest="no_filename")
    parser.add_argument("-e", action="append", dest="patterns")
    parser.add_argument("positional", nargs="*")

    parsed, unknown = parser.parse_known_args(args)
    if unknown:
        raise TerminalError(f"grep: unknown option: {unknown[0]}", exit_code=2)

    # Resolve patterns and file list.
    # With -e: all positional args are files.
    # Without -e: first positional is the pattern, rest are files.
    if parsed.patterns:
        raw_patterns = parsed.patterns
        parsed.files = parsed.positional
    else:
        if not parsed.positional:
            raise TerminalError("grep: no pattern given", exit_code=2)
        raw_patterns = [parsed.positional[0]]
        parsed.files = parsed.positional[1:]

    # -C sets both before and after context
    before_context = parsed.before_context
    after_context = parsed.after_context
    if parsed.context > 0:
        before_context = max(before_context, parsed.context)
        after_context = max(after_context, parsed.context)

    flags = 0
    if parsed.ignore_case:
        flags |= re.IGNORECASE

    # Build combined regex from all patterns (OR'd together). Without -E
    # or -F a pattern is a POSIX basic regex, the syntax agents write,
    # read into Python's: grep 'a\+' and grep -E 'a+' mean the same.
    compiled_parts = []
    for pat in raw_patterns:
        p = pat
        if parsed.fixed_strings:
            p = re.escape(p)
        elif not parsed.extended_regexp:
            p = bre_to_python(p)
        if parsed.word_regexp:
            p = r"\b" + p + r"\b"
        compiled_parts.append(p)

    combined = "|".join(f"(?:{p})" for p in compiled_parts)
    try:
        regex = re.compile(combined, flags)
    except re.error as e:
        raise TerminalError(f"grep: invalid regex: {e}", exit_code=2)

    matches_total = 0
    has_context = before_context > 0 or after_context > 0
    quiet = parsed.quiet
    real_stdout = stdout

    max_count = parsed.max_count
    if quiet and not max_count:
        max_count = 1  # -q exits after first match
    if quiet or parsed.files_without_match:
        stdout = io.StringIO()  # suppress line output

    def process_content(content: str, label: str | None) -> int:
        nonlocal matches_total
        lines = content.splitlines()
        file_matches = 0

        # Count mode
        if parsed.count:
            match_count = 0
            for line in lines:
                match = regex.search(line)
                is_match = bool(match)
                if parsed.invert_match:
                    is_match = not is_match
                if is_match:
                    match_count += 1
            matches_total += match_count
            prefix = f"{label}:" if label else ""
            stdout.write(f"{prefix}{match_count}\n")
            return match_count

        # Only-matching mode
        if parsed.only_matching:
            for i, line in enumerate(lines):
                for m in regex.finditer(line):
                    file_matches += 1
                    matches_total += 1
                    if parsed.files_with_matches:
                        if label:
                            stdout.write(f"{label}\n")
                        return file_matches
                    prefix = ""
                    if label:
                        prefix += f"{label}:"
                    if parsed.line_number:
                        prefix += f"{i + 1}:"
                    stdout.write(f"{prefix}{m.group()}\n")
                    if max_count and file_matches >= max_count:
                        return file_matches
                if max_count and file_matches >= max_count:
                    return file_matches
            return file_matches

        if has_context:
            # Context mode: need to track which lines to print
            matching_lines: set[int] = set()
            context_lines: set[int] = set()

            # First pass: find all matching lines
            for i, line in enumerate(lines):
                match = regex.search(line)
                is_match = bool(match)
                if parsed.invert_match:
                    is_match = not is_match
                if is_match:
                    matching_lines.add(i)
                    matches_total += 1
                    if parsed.files_with_matches:
                        if label:
                            stdout.write(f"{label}\n")
                        return len(matching_lines)
                    if max_count and len(matching_lines) >= max_count:
                        break

            # Second pass: mark context lines
            for match_idx in matching_lines:
                for ctx_idx in range(
                    max(0, match_idx - before_context),
                    min(len(lines), match_idx + after_context + 1),
                ):
                    if ctx_idx not in matching_lines:
                        context_lines.add(ctx_idx)

            # Third pass: output lines in order with separators
            lines_to_print = sorted(matching_lines | context_lines)
            prev_idx = -2  # Track for separator insertion

            for idx in lines_to_print:
                # Print separator if there's a gap
                if prev_idx >= 0 and idx > prev_idx + 1:
                    stdout.write("--\n")
                prev_idx = idx

                line = lines[idx]
                is_match = idx in matching_lines
                separator = ":" if is_match else "-"

                prefix = ""
                if label:
                    prefix += f"{label}{separator}"
                if parsed.line_number:
                    prefix += f"{idx + 1}{separator}"

                if prefix:
                    stdout.write(f"{prefix}{line}\n")
                else:
                    stdout.write(f"{line}\n")
        else:
            # Non-context mode: original behavior
            for i, line in enumerate(lines):
                match = regex.search(line)
                is_match = bool(match)

                if parsed.invert_match:
                    is_match = not is_match

                if is_match:
                    file_matches += 1
                    matches_total += 1
                    if parsed.files_with_matches:
                        if label:
                            stdout.write(f"{label}\n")
                        return file_matches

                    prefix = ""
                    if label:
                        prefix += f"{label}:"
                    if parsed.line_number:
                        prefix += f"{i + 1}:"

                    if prefix:
                        stdout.write(f"{prefix}{line}\n")
                    else:
                        stdout.write(f"{line}\n")

                    if max_count and file_matches >= max_count:
                        return file_matches
        return file_matches

    if not parsed.files and not parsed.recursive:
        content = stdin.read()
        process_content(content, None)
        return _matched(matches_total > 0)

    # Each path is paired with whether it came from listing a directory
    # (see _read_files): such an entry may be a directory itself.
    files_to_search: list[tuple[str, bool]] = []

    if not parsed.files:
        if parsed.recursive:
            _collect_files(".", files_to_search, fs)
    else:
        for path in parsed.files:
            if fs.isdir(path):
                # Auto-recurse into directories (matches modern grep behaviour)
                _collect_files(path, files_to_search, fs)
            else:
                files_to_search.append((path, False))

    # Apply --include/--exclude/--exclude-dir filters
    if parsed.include:
        files_to_search = [
            (f, from_listing)
            for f, from_listing in files_to_search
            if fnmatch.fnmatch(f.split("/")[-1], parsed.include)
        ]
    if parsed.exclude:
        files_to_search = [
            (f, from_listing)
            for f, from_listing in files_to_search
            if not fnmatch.fnmatch(f.split("/")[-1], parsed.exclude)
        ]
    if parsed.exclude_dir:
        files_to_search = [
            (f, from_listing)
            for f, from_listing in files_to_search
            if not any(
                fnmatch.fnmatch(part, parsed.exclude_dir) for part in f.split("/")[:-1]
            )
        ]

    reads = _read_files(fs, files_to_search)
    if parsed.recursive or not any(from_listing for _, from_listing in files_to_search):
        multiple_files = len(files_to_search) > 1 or parsed.recursive
    else:
        # A directory operand without -r: lines are labeled when there is
        # more than one file, and the listing does not say which entries
        # are files, so look two files ahead before printing any.
        ahead = list(itertools.islice(reads, 2))
        multiple_files = len(ahead) > 1
        reads = itertools.chain(ahead, reads)
    listed = False  # a file -L printed

    for filepath, content_bytes in reads:
        try:
            if isinstance(content_bytes, Exception):
                raise content_bytes
            content = content_bytes.decode("utf-8", errors="replace")

            # Determine label (filename prefix) for output lines
            if parsed.no_filename:
                label = None
            elif parsed.with_filename:
                label = filepath
            elif (
                multiple_files
                or parsed.recursive
                or parsed.files_with_matches
                or parsed.files_without_match
            ):
                label = filepath
            else:
                label = None
            file_matches = process_content(content, label)
            if parsed.files_without_match and file_matches == 0:
                real_stdout.write(f"{filepath}\n")
                listed = True

        except FileNotFoundError:
            raise TerminalError(
                f"grep: {filepath}: No such file or directory", exit_code=2
            )
        except IsADirectoryError:
            raise TerminalError(f"grep: {filepath}: Is a directory", exit_code=2)
        except Exception as e:
            raise TerminalError(f"grep: {filepath}: {e}", exit_code=2)

    # -L succeeds by naming a file; everything else by matching a line.
    return _matched(listed if parsed.files_without_match else matches_total > 0)


def _matched(found: bool) -> CommandResult | None:
    """grep's exit status: 0 when something was found, 1 when nothing
    was, silently — as GNU grep, so ``grep ... || ...`` and ``$?`` read
    the way agents expect. Errors raise with status 2 instead."""
    return None if found else CommandResult(exit_code=1, stderr="")


# ---------------------------------------------------------------------------
# find — predicate expression tree
# ---------------------------------------------------------------------------


class _FindPred:
    """Base class for find predicates."""

    __slots__ = ()

    def matches(self, item, fs=None) -> bool:
        raise NotImplementedError


class _NamePred(_FindPred):
    __slots__ = ("pattern",)

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern

    def matches(self, item, fs=None) -> bool:
        return fnmatch.fnmatch(item.name, self.pattern)


class _INamePred(_FindPred):
    """Case-insensitive name match."""

    __slots__ = ("pattern",)

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern.lower()

    def matches(self, item, fs=None) -> bool:
        return fnmatch.fnmatch(item.name.lower(), self.pattern)


class _PathPred(_FindPred):
    """Match on the full path with fnmatch."""

    __slots__ = ("pattern",)

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern

    def matches(self, item, fs=None) -> bool:
        return fnmatch.fnmatch(item.path, self.pattern)


class _DeletePred(_FindPred):
    """Action predicate: delete matched files/directories."""

    __slots__ = ()

    def matches(self, item, fs=None) -> bool:
        if fs is None:
            return True
        try:
            if item.is_dir:
                fs.rmdir(item.path)
            else:
                fs.remove(item.path)
        except Exception:
            return False
        return True


class _PrintPred(_FindPred):
    """Explicit -print action: writes the path to stdout."""

    __slots__ = ("stdout",)

    def __init__(self, stdout: TextIO) -> None:
        self.stdout = stdout

    def matches(self, item, fs=None) -> bool:
        self.stdout.write(f"{item.path}\n")
        return True


class _TypePred(_FindPred):
    __slots__ = ("kind",)

    def __init__(self, kind: str) -> None:
        self.kind = kind

    def matches(self, item, fs=None) -> bool:
        if self.kind == "f":
            return not item.is_dir
        return item.is_dir


class _EmptyPred(_FindPred):
    """Match empty files (size 0) or empty directories (no children)."""

    __slots__ = ()

    def matches(self, item, fs=None) -> bool:
        if fs is None:
            return False
        if item.is_dir:
            try:
                return len(fs.list(item.path)) == 0
            except Exception:
                return False
        return item.size == 0


class _SizePred(_FindPred):
    """Match files by size. Supports +N (greater), -N (less), N (exact).

    Suffixes: c (bytes), k (KiB), M (MiB), G (GiB). Default is 512-byte blocks.
    """

    __slots__ = ("threshold", "compare")

    def __init__(self, spec: str) -> None:
        if not spec:
            raise TerminalError("find: -size requires an argument")
        # Parse comparison operator
        if spec[0] == "+":
            self.compare = "gt"
            spec = spec[1:]
        elif spec[0] == "-":
            self.compare = "lt"
            spec = spec[1:]
        else:
            self.compare = "eq"
        # Parse suffix
        multipliers = {"c": 1, "k": 1024, "M": 1024**2, "G": 1024**3}
        if spec and spec[-1] in multipliers:
            mult = multipliers[spec[-1]]
            spec = spec[:-1]
        else:
            mult = 512  # default: 512-byte blocks
        try:
            self.threshold = int(spec) * mult
        except ValueError:
            raise TerminalError(f"find: invalid size: {spec}")

    def matches(self, item, fs=None) -> bool:
        if self.compare == "gt":
            return item.size > self.threshold
        elif self.compare == "lt":
            return item.size < self.threshold
        return item.size == self.threshold


def _emit_bytes(stdout: TextIO, data: bytes) -> None:
    """Write a sub-command's output to find's stdout byte for byte.

    The output is whatever the sub-command produced -- ``-exec cat {} +``
    over a binary file is a copy of that file -- so it must reach the
    pipe or redirect unchanged. Decoding it into find's text stream would
    replace every invalid UTF-8 sequence and hand a redirect a corrupted
    copy. The text side is flushed first so anything find itself wrote
    stays ahead of it.
    """
    stdout.flush()
    stdout.buffer.write(data)


class _ExecPred(_FindPred):
    """Run a command for each match (action predicate).

    Tokens between -exec and \\; form the command template.
    {} is replaced with the matched file path.
    Returns True if the command succeeds, False otherwise.
    When present, suppresses find's default path printing.
    """

    __slots__ = ("cmd_tokens", "stdout", "executor")

    def __init__(
        self,
        cmd_tokens: list[str],
        stdout: TextIO,
        executor: Callable[[list[str], FileSystem], bytes],
    ) -> None:
        self.cmd_tokens = cmd_tokens
        self.stdout = stdout
        self.executor = executor

    def matches(self, item, fs=None) -> bool:
        if fs is None:
            return True
        # Build command with {} replaced by the item path
        expanded = [tok.replace("{}", item.path) for tok in self.cmd_tokens]
        try:
            output = self.executor(expanded, fs)
            if output:
                _emit_bytes(self.stdout, output)
        except Exception:
            return False
        return True


class _ExecBatchPred(_FindPred):
    """Batch variant of -exec (terminated by +).

    Collects matched paths and runs a single command with all paths
    appended where {} appears. The command is executed via finalize().
    """

    __slots__ = ("cmd_tokens", "stdout", "executor", "collected")

    def __init__(
        self,
        cmd_tokens: list[str],
        stdout: TextIO,
        executor: Callable[[list[str], FileSystem], bytes],
    ) -> None:
        self.cmd_tokens = cmd_tokens
        self.stdout = stdout
        self.executor = executor
        self.collected: list[str] = []

    def matches(self, item, fs=None) -> bool:
        self.collected.append(item.path)
        return True

    def finalize(self, fs: FileSystem) -> bool:
        """Run the batched command with all collected paths; whether it
        succeeded."""
        if not self.collected:
            return True
        # Replace {} with the collected paths
        expanded: list[str] = []
        for tok in self.cmd_tokens:
            if tok == "{}":
                expanded.extend(self.collected)
            else:
                expanded.append(tok)
        try:
            output = self.executor(expanded, fs)
            if output:
                _emit_bytes(self.stdout, output)
        except Exception:
            return False
        return True


class _AndPred(_FindPred):
    __slots__ = ("left", "right")

    def __init__(self, left: _FindPred, right: _FindPred) -> None:
        self.left = left
        self.right = right

    def matches(self, item, fs=None) -> bool:
        return self.left.matches(item, fs) and self.right.matches(item, fs)


class _OrPred(_FindPred):
    __slots__ = ("left", "right")

    def __init__(self, left: _FindPred, right: _FindPred) -> None:
        self.left = left
        self.right = right

    def matches(self, item, fs=None) -> bool:
        return self.left.matches(item, fs) or self.right.matches(item, fs)


class _NotPred(_FindPred):
    __slots__ = ("child",)

    def __init__(self, child: _FindPred) -> None:
        self.child = child

    def matches(self, item, fs=None) -> bool:
        return not self.child.matches(item, fs)


class _TruePred(_FindPred):
    __slots__ = ()

    def matches(self, item, fs=None) -> bool:
        return True


def _has_action(pred: _FindPred) -> bool:
    """Check if predicate tree contains any action predicates."""
    if isinstance(pred, (_ExecPred, _ExecBatchPred, _PrintPred, _DeletePred)):
        return True
    if isinstance(pred, (_AndPred, _OrPred)):
        return _has_action(pred.left) or _has_action(pred.right)
    if isinstance(pred, _NotPred):
        return _has_action(pred.child)
    return False


def _parse_find_predicates(
    tokens: list[str],
    stdout: TextIO | None = None,
    executor: Callable[[list[str], FileSystem], bytes] | None = None,
) -> _FindPred:
    """Parse find predicate tokens into an expression tree.

    Supports: -name, -iname, -type, -size, -exec, -print,
              -not / !, -and / -a, -or / -o, ( )
    Implicit AND between adjacent predicates (like real find).
    Precedence: NOT > AND > OR.
    """
    pos = 0

    def _parse_or() -> _FindPred:
        left = _parse_and()
        nonlocal pos
        while pos < len(tokens) and tokens[pos] in ("-o", "-or"):
            pos += 1
            right = _parse_and()
            left = _OrPred(left, right)
        return left

    def _parse_and() -> _FindPred:
        left = _parse_unary()
        nonlocal pos
        while pos < len(tokens):
            tok = tokens[pos]
            # Explicit AND
            if tok in ("-a", "-and"):
                pos += 1
                right = _parse_unary()
                left = _AndPred(left, right)
            # Implicit AND: next token starts a new predicate (not an operator)
            elif tok not in ("-o", "-or", ")"):
                right = _parse_unary()
                left = _AndPred(left, right)
            else:
                break
        return left

    def _parse_unary() -> _FindPred:
        nonlocal pos
        if pos >= len(tokens):
            raise TerminalError("find: expected expression")
        tok = tokens[pos]
        if tok in ("-not", "!"):
            pos += 1
            child = _parse_unary()
            return _NotPred(child)
        return _parse_primary()

    def _parse_primary() -> _FindPred:
        nonlocal pos
        if pos >= len(tokens):
            raise TerminalError("find: expected expression")
        tok = tokens[pos]

        if tok == "(":
            pos += 1
            node = _parse_or()
            if pos >= len(tokens) or tokens[pos] != ")":
                raise TerminalError("find: missing closing ')'")
            pos += 1
            return node

        if tok == "-name":
            pos += 1
            if pos >= len(tokens):
                raise TerminalError("find: -name requires a pattern")
            pattern = tokens[pos]
            pos += 1
            return _NamePred(pattern)

        if tok == "-iname":
            pos += 1
            if pos >= len(tokens):
                raise TerminalError("find: -iname requires a pattern")
            pattern = tokens[pos]
            pos += 1
            return _INamePred(pattern)

        if tok == "-path":
            pos += 1
            if pos >= len(tokens):
                raise TerminalError("find: -path requires a pattern")
            pattern = tokens[pos]
            pos += 1
            return _PathPred(pattern)

        if tok == "-print":
            pos += 1
            return _PrintPred(stdout)

        if tok == "-delete":
            pos += 1
            return _DeletePred()

        if tok == "-type":
            pos += 1
            if pos >= len(tokens):
                raise TerminalError("find: -type requires an argument")
            kind = tokens[pos]
            if kind not in ("f", "d"):
                raise TerminalError(f"find: unknown type '{kind}' (use 'f' or 'd')")
            pos += 1
            return _TypePred(kind)

        if tok == "-empty":
            pos += 1
            return _EmptyPred()

        if tok == "-size":
            pos += 1
            if pos >= len(tokens):
                raise TerminalError("find: -size requires an argument")
            spec = tokens[pos]
            pos += 1
            # Handle shell splitting: +1k → ["+", "1k"] or -100c → ["-", "100c"]
            if spec in ("+", "-") and pos < len(tokens):
                spec = spec + tokens[pos]
                pos += 1
            return _SizePred(spec)

        if tok == "-exec":
            pos += 1
            cmd_tokens: list[str] = []
            while pos < len(tokens) and tokens[pos] not in (";", "+"):
                cmd_tokens.append(tokens[pos])
                pos += 1
            if pos >= len(tokens):
                raise TerminalError("find: -exec requires terminating ';' or '+'")
            batch = tokens[pos] == "+"
            pos += 1  # skip ; or +
            if not cmd_tokens:
                raise TerminalError("find: -exec requires a command")
            if executor is None:
                raise TerminalError("find: -exec not available in this context")
            if batch:
                return _ExecBatchPred(cmd_tokens, stdout, executor)
            return _ExecPred(cmd_tokens, stdout, executor)

        raise TerminalError(f"find: unknown predicate: {tok}")

    if not tokens:
        return _TruePred()

    result = _parse_or()
    if pos < len(tokens):
        raise TerminalError(f"find: unexpected token: {tokens[pos]}")
    return result


def _finalize_batch(pred: _FindPred, fs: FileSystem) -> bool:
    """Walk the predicate tree and finalize any batch -exec + predicates;
    whether every batch succeeded."""
    if isinstance(pred, _ExecBatchPred):
        return pred.finalize(fs)
    if isinstance(pred, (_AndPred, _OrPred)):
        left = _finalize_batch(pred.left, fs)
        right = _finalize_batch(pred.right, fs)
        return left and right
    if isinstance(pred, _NotPred):
        return _finalize_batch(pred.child, fs)
    return True


def find(ctx: CommandContext) -> CommandResult | None:
    """Search for files in a directory hierarchy."""
    args, _stdin, stdout, fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    # Separate path, global options (-maxdepth, -mindepth), and predicates.
    # We can't use argparse because predicate tokens like -o look like flags.
    root_path = "."
    maxdepth = None
    mindepth = None
    predicate_tokens: list[str] = []

    i = 0
    # Leading positional arg (path) — anything not starting with - or (
    if i < len(args) and not args[i].startswith("-") and args[i] not in ("(", "!"):
        root_path = args[i]
        i += 1

    # Consume global options and collect predicate tokens
    while i < len(args):
        if args[i] == "-maxdepth":
            i += 1
            if i >= len(args):
                raise TerminalError("find: -maxdepth requires an argument")
            try:
                maxdepth = int(args[i])
            except ValueError:
                raise TerminalError(f"find: invalid argument to -maxdepth: {args[i]}")
            i += 1
        elif args[i] == "-mindepth":
            i += 1
            if i >= len(args):
                raise TerminalError("find: -mindepth requires an argument")
            try:
                mindepth = int(args[i])
            except ValueError:
                raise TerminalError(f"find: invalid argument to -mindepth: {args[i]}")
            i += 1
        else:
            predicate_tokens.append(args[i])
            i += 1

    # What -exec commands say on stderr is find's own stderr, shown and
    # never fed down the pipe: the warnings of commands that succeed and
    # the diagnostics of those that fail.
    exec_stderr: list[str] = []

    def _executor(argv: list[str], executor_fs: FileSystem) -> bytes:
        """Dispatch an already-tokenized command without shell reparsing.

        Returns the sub-command's stdout as bytes, so a binary payload
        survives the trip through find unchanged.
        """
        try:
            output, warning = dispatch_argv(argv, executor_fs, ctx.env)
        except TerminalError as e:
            if text := stderr_of(e):
                exec_stderr.append(text)
            raise
        if warning:
            exec_stderr.append(warning)
        return output

    predicate = _parse_find_predicates(
        predicate_tokens, stdout=stdout, executor=_executor
    )
    has_action = _has_action(predicate)

    try:
        items = fs.list_detailed(root_path, recursive=True)
    except Exception as e:
        raise TerminalError(f"find: {e}")

    prefix = root_path.rstrip("/")

    for item in items:
        # Compute depth from relative path (strip the user-provided prefix)
        rel = (
            item.path[len(prefix) + 1 :]
            if item.path.startswith(prefix + "/")
            else item.name
        )
        depth = len(rel.split("/"))

        if maxdepth is not None and depth > maxdepth:
            continue
        if mindepth is not None and depth < mindepth:
            continue

        try:
            if not predicate.matches(item, fs):
                continue
        except TerminalError:
            raise
        except Exception:
            continue

        # Suppress default path printing when action predicates exist
        if not has_action:
            stdout.write(f"{item.path}\n")

    # Finalize any batch -exec + predicates. A failed '+' batch makes find
    # exit 1, as GNU's does; a failed ';' command only makes its test false.
    batches_ok = _finalize_batch(predicate, fs)
    if exec_stderr or not batches_ok:
        return CommandResult(
            exit_code=0 if batches_ok else 1, stderr=join_stderr(exec_stderr)
        )
    return None
