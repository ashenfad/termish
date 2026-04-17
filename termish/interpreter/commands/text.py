"""
Text processing commands for the terminal interpreter.
"""

from termish.context import CommandContext, CommandResult
from termish.errors import TerminalError

from ._argparse import CommandArgParser


def wc(ctx: CommandContext) -> CommandResult | None:
    """Word, line, character, and byte count."""
    args, stdin, stdout, fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    parser = CommandArgParser(prog="wc", add_help=False)
    parser.add_argument("-l", "--lines", action="store_true")
    parser.add_argument("-w", "--words", action="store_true")
    parser.add_argument("-c", "--bytes", action="store_true")
    parser.add_argument("-m", "--chars", action="store_true")
    parser.add_argument("-L", "--max-line-length", action="store_true")
    parser.add_argument("files", nargs="*")

    parsed, unknown = parser.parse_known_args(args)
    if unknown:
        raise TerminalError(f"wc: unknown option: {unknown[0]}")

    # If no flags specified, show all three (lines, words, bytes)
    show_lines = parsed.lines
    show_words = parsed.words
    show_bytes = parsed.bytes or parsed.chars  # -m same as -c for UTF-8
    show_max_line = parsed.max_line_length
    if not (show_lines or show_words or show_bytes or show_max_line):
        show_lines = show_words = show_bytes = True

    totals = {"lines": 0, "words": 0, "bytes": 0, "max_line": 0}
    results: list[tuple[dict[str, int], str]] = []

    def count_content(content: str, name: str):
        lines = content.splitlines()
        max_line = max((len(line) for line in lines), default=0)
        counts = {
            "lines": content.count("\n"),
            "words": len(content.split()),
            "bytes": len(content.encode("utf-8")),
            "max_line": max_line,
        }
        results.append((counts, name))
        for key in totals:
            if key == "max_line":
                totals[key] = max(totals[key], counts[key])
            else:
                totals[key] += counts[key]

    if not parsed.files:
        # Read from stdin
        content = stdin.read()
        count_content(content, "")
    else:
        for path in parsed.files:
            try:
                content_bytes = fs.read(path)
                content = content_bytes.decode("utf-8", errors="replace")
                count_content(content, path)
            except FileNotFoundError:
                raise TerminalError(f"wc: {path}: No such file or directory")
            except IsADirectoryError:
                raise TerminalError(f"wc: {path}: Is a directory")

    # Calculate width for formatting (based on largest number)
    max_val = max(totals.values()) if totals["bytes"] > 0 else 1
    width = len(str(max_val))

    def format_line(counts: dict[str, int], name: str) -> str:
        parts = []
        if show_lines:
            parts.append(f"{counts['lines']:>{width}}")
        if show_words:
            parts.append(f"{counts['words']:>{width}}")
        if show_bytes:
            parts.append(f"{counts['bytes']:>{width}}")
        if show_max_line:
            parts.append(f"{counts['max_line']:>{width}}")
        line = " ".join(parts)
        if name:
            line += f" {name}"
        return line

    for counts, name in results:
        stdout.write(format_line(counts, name) + "\n")

    # Show total if multiple files
    if len(results) > 1:
        stdout.write(format_line(totals, "total") + "\n")


def sort(ctx: CommandContext) -> CommandResult | None:
    """Sort lines of text."""
    args, stdin, stdout, fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    parser = CommandArgParser(prog="sort", add_help=False)
    parser.add_argument("-r", "--reverse", action="store_true")
    parser.add_argument("-n", "--numeric-sort", action="store_true")
    parser.add_argument("-u", "--unique", action="store_true")
    parser.add_argument("-f", "--ignore-case", action="store_true")
    parser.add_argument("-k", "--key", action="append", dest="keys", default=None)
    parser.add_argument("-t", "--field-separator", type=str, default=None)
    parser.add_argument("files", nargs="*")

    parsed, unknown = parser.parse_known_args(args)
    if unknown:
        raise TerminalError(f"sort: unknown option: {unknown[0]}")

    # Collect all lines
    lines: list[str] = []
    if not parsed.files:
        lines = stdin.read().splitlines()
    else:
        for path in parsed.files:
            try:
                content_bytes = fs.read(path)
                content = content_bytes.decode("utf-8", errors="replace")
                lines.extend(content.splitlines())
            except FileNotFoundError:
                raise TerminalError(f"sort: {path}: No such file or directory")
            except IsADirectoryError:
                raise TerminalError(f"sort: {path}: Is a directory")

    # Parse -k field numbers (1-indexed), supports multiple -k flags
    field_nums: list[int] = []
    if parsed.keys:
        for key_spec in parsed.keys:
            try:
                field_nums.append(int(key_spec.split(",")[0].split(".")[0]))
            except ValueError:
                raise TerminalError(f"sort: invalid field specification: {key_spec}")

    def make_key(line: str):
        if not field_nums:
            val = line
            if parsed.ignore_case:
                val = val.lower()
            if parsed.numeric_sort:
                try:
                    return (0, float(val))
                except ValueError:
                    return (1, val)
            return val

        if parsed.field_separator:
            fields = line.split(parsed.field_separator)
        else:
            fields = line.split()

        key_parts: list = []
        for fn in field_nums:
            val = fields[fn - 1] if fn <= len(fields) else ""
            if parsed.ignore_case:
                val = val.lower()
            if parsed.numeric_sort:
                try:
                    key_parts.append((0, float(val)))
                except ValueError:
                    key_parts.append((1, val))
            else:
                key_parts.append(val)
        return tuple(key_parts)

    # Sort with stable sort
    sorted_lines = sorted(lines, key=make_key, reverse=parsed.reverse)

    # Deduplicate if -u (preserve first occurrence based on sort key)
    if parsed.unique:
        seen_keys: set = set()
        unique_lines: list[str] = []
        for line in sorted_lines:
            key = make_key(line)
            if key not in seen_keys:
                seen_keys.add(key)
                unique_lines.append(line)
        sorted_lines = unique_lines

    for line in sorted_lines:
        stdout.write(line + "\n")


def uniq(ctx: CommandContext) -> CommandResult | None:
    """Report or omit repeated lines."""
    args, stdin, stdout, fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    parser = CommandArgParser(prog="uniq", add_help=False)
    parser.add_argument("-c", "--count", action="store_true")
    parser.add_argument("-d", "--repeated", action="store_true")
    parser.add_argument("-u", "--unique", action="store_true")
    parser.add_argument("-i", "--ignore-case", action="store_true")
    parser.add_argument("file", nargs="?")

    parsed, unknown = parser.parse_known_args(args)
    if unknown:
        raise TerminalError(f"uniq: unknown option: {unknown[0]}")

    # Read input
    if parsed.file:
        try:
            content_bytes = fs.read(parsed.file)
            content = content_bytes.decode("utf-8", errors="replace")
            lines = content.splitlines()
        except FileNotFoundError:
            raise TerminalError(f"uniq: {parsed.file}: No such file or directory")
        except IsADirectoryError:
            raise TerminalError(f"uniq: {parsed.file}: Is a directory")
    else:
        lines = stdin.read().splitlines()

    if not lines:
        return

    def compare_key(line: str) -> str:
        return line.lower() if parsed.ignore_case else line

    # Group adjacent identical lines
    groups: list[tuple[int, str]] = []  # (count, original_line)
    current_line = lines[0]
    current_key = compare_key(current_line)
    count = 1

    for line in lines[1:]:
        key = compare_key(line)
        if key == current_key:
            count += 1
        else:
            groups.append((count, current_line))
            current_line = line
            current_key = key
            count = 1
    groups.append((count, current_line))

    # Output based on flags
    for count, line in groups:
        # -d: only print duplicates (count > 1)
        if parsed.repeated and count == 1:
            continue
        # -u: only print unique (count == 1)
        if parsed.unique and count > 1:
            continue

        if parsed.count:
            stdout.write(f"{count:7d} {line}\n")
        else:
            stdout.write(line + "\n")


def cut(ctx: CommandContext) -> CommandResult | None:
    """Remove sections from each line of files."""
    args, stdin, stdout, fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    parser = CommandArgParser(prog="cut", add_help=False)
    parser.add_argument("-d", "--delimiter", type=str, default="\t")
    parser.add_argument("-f", "--fields", type=str, default=None)
    parser.add_argument("-c", "--characters", type=str, default=None)
    parser.add_argument("-b", "--bytes", type=str, default=None)
    parser.add_argument("--complement", action="store_true")
    parser.add_argument("--output-delimiter", type=str, default=None)
    parser.add_argument("files", nargs="*")

    parsed, unknown = parser.parse_known_args(args)
    if unknown:
        raise TerminalError(f"cut: unknown option: {unknown[0]}")

    # Interpret common escape sequences in delimiter (no $'...' in shell parser)
    parsed.delimiter = parsed.delimiter.replace("\\t", "\t").replace("\\n", "\n")

    # Must specify one of -f, -c, or -b
    if not (parsed.fields or parsed.characters or parsed.bytes):
        raise TerminalError(
            "cut: you must specify -f (fields), -c (characters), or -b (bytes) "
            "(e.g., cut -d ',' -f 1 file.txt)"
        )

    def parse_ranges(spec: str) -> list[tuple[int | None, int | None]]:
        """Parse field/char specification like '1,3-5,7-' into ranges."""
        ranges: list[tuple[int | None, int | None]] = []
        for part in spec.split(","):
            part = part.strip()
            if "-" in part:
                start_str, end_str = part.split("-", 1)
                start = int(start_str) if start_str else 1
                end = int(end_str) if end_str else None
                ranges.append((start, end))
            else:
                n = int(part)
                ranges.append((n, n))
        return ranges

    def select_items(
        items: list[str], ranges: list[tuple[int | None, int | None]], complement: bool
    ) -> list[str]:
        """Select items based on 1-indexed ranges."""
        if complement:
            # Select all indices NOT in ranges
            excluded: set[int] = set()
            for start, end in ranges:
                if end is None:
                    end = len(items)
                for i in range(start, end + 1):
                    excluded.add(i)
            return [items[i] for i in range(len(items)) if (i + 1) not in excluded]
        else:
            result: list[str] = []
            for start, end in ranges:
                if end is None:
                    result.extend(items[start - 1 :])
                else:
                    result.extend(items[start - 1 : end])
            return result

    # Parse the ranges
    if parsed.fields:
        try:
            ranges = parse_ranges(parsed.fields)
        except ValueError:
            raise TerminalError(f"cut: invalid field specification: {parsed.fields}")
        mode = "fields"
    elif parsed.characters:
        try:
            ranges = parse_ranges(parsed.characters)
        except ValueError:
            raise TerminalError(
                f"cut: invalid character specification: {parsed.characters}"
            )
        mode = "chars"
    else:  # bytes
        try:
            ranges = parse_ranges(parsed.bytes)
        except ValueError:
            raise TerminalError(f"cut: invalid byte specification: {parsed.bytes}")
        mode = "chars"  # Same as chars for our purposes

    # Collect lines
    lines: list[str] = []
    if not parsed.files:
        lines = stdin.read().splitlines()
    else:
        for path in parsed.files:
            try:
                content_bytes = fs.read(path)
                content = content_bytes.decode("utf-8", errors="replace")
                lines.extend(content.splitlines())
            except FileNotFoundError:
                raise TerminalError(f"cut: {path}: No such file or directory")
            except IsADirectoryError:
                raise TerminalError(f"cut: {path}: Is a directory")

    # Determine output delimiter
    out_delim = (
        parsed.output_delimiter
        if parsed.output_delimiter is not None
        else parsed.delimiter
    )

    # Process each line
    for line in lines:
        if mode == "fields":
            fields = line.split(parsed.delimiter)
            selected = select_items(fields, ranges, parsed.complement)
            stdout.write(out_delim.join(selected) + "\n")
        else:  # chars/bytes
            chars = list(line)
            selected = select_items(chars, ranges, parsed.complement)
            stdout.write("".join(selected) + "\n")


# ---------------------------------------------------------------------------
# tr
# ---------------------------------------------------------------------------


def _expand_tr_set(s: str) -> str:
    """Expand character set notation for tr (ranges and character classes)."""
    classes = {
        "[:upper:]": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "[:lower:]": "abcdefghijklmnopqrstuvwxyz",
        "[:digit:]": "0123456789",
        "[:alpha:]": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
        "[:alnum:]": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
        "[:space:]": " \t\n\r\f\v",
        "[:blank:]": " \t",
    }

    result: list[str] = []
    i = 0
    while i < len(s):
        # Check for character classes
        if s[i] == "[" and i + 1 < len(s) and s[i + 1] == ":":
            found = False
            for cls_name, cls_chars in classes.items():
                if s[i:].startswith(cls_name):
                    result.append(cls_chars)
                    i += len(cls_name)
                    found = True
                    break
            if found:
                continue

        # Check for escape sequences (before ranges, so \-z isn't a range)
        if s[i] == "\\" and i + 1 < len(s):
            match s[i + 1]:
                case "n":
                    result.append("\n")
                case "t":
                    result.append("\t")
                case "\\":
                    result.append("\\")
                case other:
                    result.append(other)
            i += 2
            continue

        # Check for ranges like a-z
        if i + 2 < len(s) and s[i + 1] == "-":
            start_ord = ord(s[i])
            end_ord = ord(s[i + 2])
            if start_ord <= end_ord:
                for c in range(start_ord, end_ord + 1):
                    result.append(chr(c))
                i += 3
                continue

        result.append(s[i])
        i += 1

    return "".join(result)


def tr(ctx: CommandContext) -> CommandResult | None:
    """Translate or delete characters."""
    args, stdin, stdout, _fs = ctx.args, ctx.stdin, ctx.stdout, ctx.fs
    delete = False
    squeeze = False
    complement = False

    # Manual flag parsing
    text_args: list[str] = []
    for arg in args:
        if arg.startswith("-") and len(arg) > 1 and not text_args:
            for ch in arg[1:]:
                match ch:
                    case "d":
                        delete = True
                    case "s":
                        squeeze = True
                    case "c" | "C":
                        complement = True
                    case _:
                        raise TerminalError(f"tr: unknown option: -{ch}")
        else:
            text_args.append(arg)

    if not text_args:
        raise TerminalError("tr: missing operand")

    set1 = _expand_tr_set(text_args[0])
    set2 = _expand_tr_set(text_args[1]) if len(text_args) > 1 else ""

    content = stdin.read()

    if complement:
        all_chars = sorted(set(content))
        set1_chars = set(set1)
        set1 = "".join(c for c in all_chars if c not in set1_chars)

    if delete:
        chars_to_delete = set(set1)
        result = "".join(c for c in content if c not in chars_to_delete)
        if squeeze and set2:
            squeezed: list[str] = []
            squeeze_set = set(set2)
            for ch in result:
                if ch in squeeze_set and squeezed and squeezed[-1] == ch:
                    continue
                squeezed.append(ch)
            result = "".join(squeezed)
    elif squeeze and not set2:
        squeezed = []
        squeeze_set = set(set1)
        for ch in content:
            if ch in squeeze_set and squeezed and squeezed[-1] == ch:
                continue
            squeezed.append(ch)
        result = "".join(squeezed)
    else:
        if not set2:
            raise TerminalError("tr: missing operand after SET1")
        # Pad set2 to match set1 length (repeat last char)
        if len(set2) < len(set1):
            set2 = set2 + set2[-1] * (len(set1) - len(set2))
        table = str.maketrans(set1, set2[: len(set1)])
        result = content.translate(table)
        if squeeze:
            squeezed = []
            squeeze_set = set(set2)
            for ch in result:
                if ch in squeeze_set and squeezed and squeezed[-1] == ch:
                    continue
                squeezed.append(ch)
            result = "".join(squeezed)

    stdout.write(result)
