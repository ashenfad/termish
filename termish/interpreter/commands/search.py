"Search commands (grep, find) for the terminal interpreter."

import fnmatch
import re
from typing import TextIO

from termish.errors import TerminalError
from termish.fs import FileSystem

from ._argparse import CommandArgParser


def grep(args: list[str], stdin: TextIO, stdout: TextIO, fs: FileSystem) -> None:
    """Print lines that match patterns."""
    parser = CommandArgParser(prog="grep", add_help=False)
    parser.add_argument("-i", "--ignore-case", action="store_true")
    parser.add_argument("-n", "--line-number", action="store_true")
    parser.add_argument("-r", "-R", "--recursive", action="store_true")
    parser.add_argument("-l", "--files-with-matches", action="store_true")
    parser.add_argument("-v", "--invert-match", action="store_true")
    parser.add_argument("-F", "--fixed-strings", action="store_true")
    parser.add_argument("-E", "--extended-regexp", action="store_true")
    parser.add_argument("-A", "--after-context", type=int, default=0)
    parser.add_argument("-B", "--before-context", type=int, default=0)
    parser.add_argument("-C", "--context", type=int, default=0)
    parser.add_argument("-c", "--count", action="store_true")
    parser.add_argument("-w", "--word-regexp", action="store_true")
    parser.add_argument("-o", "--only-matching", action="store_true")
    parser.add_argument("-m", "--max-count", type=int, default=0)
    parser.add_argument("--include", type=str, default=None)
    parser.add_argument("--exclude", type=str, default=None)
    parser.add_argument("--exclude-dir", type=str, default=None)
    parser.add_argument("-e", action="append", dest="patterns")
    parser.add_argument("positional", nargs="*")

    parsed, unknown = parser.parse_known_args(args)
    if unknown:
        raise TerminalError(f"grep: unknown option: {unknown[0]}")

    # Resolve patterns and file list.
    # With -e: all positional args are files.
    # Without -e: first positional is the pattern, rest are files.
    if parsed.patterns:
        raw_patterns = parsed.patterns
        parsed.files = parsed.positional
    else:
        if not parsed.positional:
            raise TerminalError("grep: no pattern given")
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

    # Build combined regex from all patterns (OR'd together)
    compiled_parts = []
    for pat in raw_patterns:
        p = pat
        if parsed.fixed_strings:
            p = re.escape(p)
        if parsed.word_regexp:
            p = r"\b" + p + r"\b"
        compiled_parts.append(p)

    combined = "|".join(f"(?:{p})" for p in compiled_parts)
    try:
        regex = re.compile(combined, flags)
    except re.error as e:
        raise TerminalError(f"grep: invalid regex: {e}")

    matches_total = 0
    has_context = before_context > 0 or after_context > 0

    max_count = parsed.max_count

    def process_content(content: str, label: str | None) -> None:
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
            return

        # Only-matching mode
        if parsed.only_matching:
            for i, line in enumerate(lines):
                for m in regex.finditer(line):
                    file_matches += 1
                    matches_total += 1
                    if parsed.files_with_matches:
                        if label:
                            stdout.write(f"{label}\n")
                        return
                    prefix = ""
                    if label:
                        prefix += f"{label}:"
                    if parsed.line_number:
                        prefix += f"{i + 1}:"
                    stdout.write(f"{prefix}{m.group()}\n")
                    if max_count and file_matches >= max_count:
                        return
                if max_count and file_matches >= max_count:
                    return
            return

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
                        return
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
                        return  # Stop processing this file

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
                        return

    if not parsed.files and not parsed.recursive:
        content = stdin.read()
        process_content(content, None)
        return

    files_to_search = []

    if not parsed.files:
        if parsed.recursive:
            root = "."
            try:
                all_files = fs.list_detailed(root, recursive=True)
                for f in all_files:
                    if not f.is_dir:
                        files_to_search.append(f.path)
            except Exception as e:
                raise TerminalError(f"grep: {e}")
    else:
        for path in parsed.files:
            if fs.isdir(path):
                if parsed.recursive:
                    try:
                        all_files = fs.list_detailed(path, recursive=True)
                        for f in all_files:
                            if not f.is_dir:
                                files_to_search.append(f.path)
                    except Exception as e:
                        raise TerminalError(f"grep: {path}: {e}")
                else:
                    raise TerminalError(f"grep: {path}: Is a directory")
            else:
                files_to_search.append(path)

    # Apply --include/--exclude/--exclude-dir filters
    if parsed.include:
        files_to_search = [
            f
            for f in files_to_search
            if fnmatch.fnmatch(f.split("/")[-1], parsed.include)
        ]
    if parsed.exclude:
        files_to_search = [
            f
            for f in files_to_search
            if not fnmatch.fnmatch(f.split("/")[-1], parsed.exclude)
        ]
    if parsed.exclude_dir:
        files_to_search = [
            f
            for f in files_to_search
            if not any(
                fnmatch.fnmatch(part, parsed.exclude_dir) for part in f.split("/")[:-1]
            )
        ]

    multiple_files = len(files_to_search) > 1 or parsed.recursive

    for filepath in files_to_search:
        try:
            content_bytes = fs.read(filepath)
            content = content_bytes.decode("utf-8", errors="replace")

            # -l always needs the filepath as label
            label = (
                filepath
                if (multiple_files or parsed.recursive or parsed.files_with_matches)
                else None
            )
            process_content(content, label)

        except FileNotFoundError:
            raise TerminalError(f"grep: {filepath}: No such file or directory")
        except IsADirectoryError:
            raise TerminalError(f"grep: {filepath}: Is a directory")
        except Exception as e:
            raise TerminalError(f"grep: {filepath}: {e}")


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


class _ExecPred(_FindPred):
    """Run a command for each match (action predicate).

    Tokens between -exec and \\; form the command template.
    {} is replaced with the matched file path.
    Returns True if the command succeeds, False otherwise.
    When present, suppresses find's default path printing.
    """

    __slots__ = ("cmd_tokens", "stdout")

    def __init__(self, cmd_tokens: list[str], stdout: TextIO) -> None:
        self.cmd_tokens = cmd_tokens
        self.stdout = stdout

    def matches(self, item, fs=None) -> bool:
        if fs is None:
            return True
        from termish.interpreter.core import execute_script
        from termish.parser import to_script

        # Build command with {} replaced by the item path
        expanded = [tok.replace("{}", item.path) for tok in self.cmd_tokens]
        cmd_str = " ".join(expanded)
        try:
            output = execute_script(to_script(cmd_str), fs)
            if output:
                self.stdout.write(output)
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
    """Check if predicate tree contains any action predicates (like -exec, -print)."""
    if isinstance(pred, (_ExecPred, _PrintPred)):
        return True
    if isinstance(pred, (_AndPred, _OrPred)):
        return _has_action(pred.left) or _has_action(pred.right)
    if isinstance(pred, _NotPred):
        return _has_action(pred.child)
    return False


def _parse_find_predicates(
    tokens: list[str], stdout: TextIO | None = None
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

        if tok == "-print":
            pos += 1
            return _PrintPred(stdout)

        if tok == "-type":
            pos += 1
            if pos >= len(tokens):
                raise TerminalError("find: -type requires an argument")
            kind = tokens[pos]
            if kind not in ("f", "d"):
                raise TerminalError(f"find: unknown type '{kind}' (use 'f' or 'd')")
            pos += 1
            return _TypePred(kind)

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
            while pos < len(tokens) and tokens[pos] != ";":
                cmd_tokens.append(tokens[pos])
                pos += 1
            if pos >= len(tokens):
                raise TerminalError("find: -exec requires terminating ';'")
            pos += 1  # skip ;
            if not cmd_tokens:
                raise TerminalError("find: -exec requires a command")
            return _ExecPred(cmd_tokens, stdout)

        raise TerminalError(f"find: unknown predicate: {tok}")

    if not tokens:
        return _TruePred()

    result = _parse_or()
    if pos < len(tokens):
        raise TerminalError(f"find: unexpected token: {tokens[pos]}")
    return result


def find(args: list[str], stdin: TextIO, stdout: TextIO, fs: FileSystem) -> None:
    """Search for files in a directory hierarchy."""
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

    predicate = _parse_find_predicates(predicate_tokens, stdout=stdout)
    has_action = _has_action(predicate)

    try:
        all_items = fs.list_detailed(root_path, recursive=True)

        # Calculate base for depth computation
        root_stripped = root_path.rstrip("/") if root_path != "/" else ""

        for item in all_items:
            # Calculate depth relative to root
            relative = item.path[len(root_stripped) :].lstrip("/")
            depth = len(relative.split("/")) if relative else 0

            if maxdepth is not None and depth > maxdepth:
                continue
            if mindepth is not None and depth < mindepth:
                continue

            if not predicate.matches(item, fs):
                continue

            # Suppress default path printing when action predicates exist
            if not has_action:
                stdout.write(f"{item.path}\n")

    except TerminalError:
        raise
    except Exception as e:
        raise TerminalError(f"find: {e}")
