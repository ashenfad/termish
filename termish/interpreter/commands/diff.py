"""
Diff command for the terminal interpreter.
"""

import difflib
from typing import TextIO

from termish.errors import TerminalError
from termish.fs import FileSystem

from ._argparse import CommandArgParser


def diff(args: list[str], stdin: TextIO, stdout: TextIO, fs: FileSystem) -> None:
    """Compare files line by line."""
    parser = CommandArgParser(prog="diff", add_help=False)
    parser.add_argument("-u", "--unified", action="store_true")
    parser.add_argument("-c", "--context", action="store_true")
    parser.add_argument("-q", "--brief", action="store_true")
    parser.add_argument("-B", "--ignore-blank-lines", action="store_true")
    parser.add_argument("-w", "--ignore-all-space", action="store_true")
    parser.add_argument("-i", "--ignore-case", action="store_true")
    parser.add_argument("file1", nargs="?")
    parser.add_argument("file2", nargs="?")

    parsed, unknown = parser.parse_known_args(args)
    if unknown:
        raise TerminalError(f"diff: unknown option: {unknown[0]}")

    if not parsed.file1 or not parsed.file2:
        raise TerminalError(
            "diff: requires two file arguments (e.g., diff file1.txt file2.txt)"
        )

    # Read files
    def read_file(path: str) -> list[str]:
        try:
            content_bytes = fs.read(path)
            content = content_bytes.decode("utf-8", errors="replace")
            return content.splitlines(keepends=True)
        except FileNotFoundError:
            raise TerminalError(f"diff: {path}: No such file or directory")
        except IsADirectoryError:
            raise TerminalError(f"diff: {path}: Is a directory")

    file1_lines = read_file(parsed.file1)
    file2_lines = read_file(parsed.file2)

    # Apply preprocessing if needed
    def preprocess(lines: list[str]) -> list[str]:
        result = lines
        if parsed.ignore_blank_lines:
            result = [line for line in result if line.strip()]
        if parsed.ignore_all_space:
            result = [line.replace(" ", "").replace("\t", "") for line in result]
        if parsed.ignore_case:
            result = [line.lower() for line in result]
        return result

    if parsed.ignore_blank_lines or parsed.ignore_all_space or parsed.ignore_case:
        cmp1 = preprocess(file1_lines)
        cmp2 = preprocess(file2_lines)
    else:
        cmp1 = file1_lines
        cmp2 = file2_lines

    # Brief mode
    if parsed.brief:
        if cmp1 != cmp2:
            stdout.write(f"Files {parsed.file1} and {parsed.file2} differ\n")
        return

    # Generate diff: compare preprocessed lines but display originals.
    # When preprocessing is active, generate diff output from preprocessed
    # lines (to get correct hunks), then substitute original lines.
    if parsed.context:
        diff_lines = list(
            difflib.context_diff(cmp1, cmp2, fromfile=parsed.file1, tofile=parsed.file2)
        )
    else:
        diff_lines = list(
            difflib.unified_diff(cmp1, cmp2, fromfile=parsed.file1, tofile=parsed.file2)
        )

    if cmp1 is not file1_lines and diff_lines:
        # Build mapping that handles duplicate preprocessed lines
        # (e.g. "a" and "A" both becoming "a" with -i) by keeping
        # all originals in order and popping the first match.
        from collections import defaultdict

        cmp_map: dict[str, list[str]] = defaultdict(list)
        for prep, orig in zip(cmp1, file1_lines):
            cmp_map[prep].append(orig)
        for prep, orig in zip(cmp2, file2_lines):
            cmp_map[prep].append(orig)
        for i, line in enumerate(diff_lines):
            if line.startswith(("---", "+++", "@@", "***", "--- ", "+++ ")):
                continue
            prefix = ""
            content = line
            if line and line[0] in ("-", "+", "!", " "):
                prefix = line[0]
                content = line[1:]
            originals = cmp_map.get(content)
            original = originals.pop(0) if originals else content
            diff_lines[i] = prefix + original

    for line in diff_lines:
        stdout.write(line)
