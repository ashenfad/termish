"""What ``help`` says of each builtin: a one-line summary for the list,
and its usage for ``help NAME``. The same text as termish-ts's
``help-text.ts``, entry for entry (``jq`` is termish-py's alone).
"""

from typing import NamedTuple


class HelpEntry(NamedTuple):
    summary: str
    usage: str


HELP: dict[str, HelpEntry] = {
    "[": HelpEntry(
        "evaluate a condition (same as test)",
        "[ EXPRESSION ]",
    ),
    "basename": HelpEntry(
        "strip the directory from a path",
        "basename PATH [SUFFIX]",
    ),
    "cat": HelpEntry(
        "print files",
        "cat [-n] [-A | -e | -T] [FILE...]",
    ),
    "cd": HelpEntry(
        "change the working directory",
        "cd [DIR]",
    ),
    "comm": HelpEntry(
        "compare two sorted files line by line",
        "comm [-1] [-2] [-3] FILE1 FILE2",
    ),
    "command": HelpEntry(
        "run a command, or say whether one exists",
        "command -v NAME... | command -V NAME... | command NAME [ARG...]",
    ),
    "cp": HelpEntry(
        "copy files and directories",
        "cp [-r] [-a] SOURCE... DEST",
    ),
    "cut": HelpEntry(
        "select fields or characters from each line",
        "cut (-f LIST [-d DELIM] | -c LIST | -b LIST) [--complement] [FILE...]",
    ),
    "date": HelpEntry(
        "print the current date and time",
        "date [-u] [+FORMAT | -I[date|hours|minutes|seconds] | -R]",
    ),
    "diff": HelpEntry(
        "compare files line by line",
        "diff [-u | -U N | -c | -q] [-r] [-i] [-w] [-b] [-B] FILE1 FILE2",
    ),
    "dirname": HelpEntry(
        "strip the last component from a path",
        "dirname PATH",
    ),
    "du": HelpEntry(
        "sum file sizes under directories (file bytes, not disk blocks)",
        "du [-s | -a] [-c] [-h] [PATH...]",
    ),
    "echo": HelpEntry(
        "print arguments",
        "echo [-n] [-e] [ARG...]",
    ),
    "false": HelpEntry(
        "fail, doing nothing",
        "false",
    ),
    "file": HelpEntry(
        "guess a file's type from its contents",
        "file FILE...",
    ),
    "find": HelpEntry(
        "search a directory tree",
        "find [PATH...] [-maxdepth N] [-mindepth N] [-name P] [-iname P] [-path P] [-type f|d] [-size N] [-empty] [-print] [-delete] [-exec CMD {} ;]",
    ),
    "grep": HelpEntry(
        "search for a pattern in lines",
        "grep [-i] [-n] [-r] [-l | -L | -c | -o | -q] [-v] [-w] [-E | -F] [-A N] [-B N] [-C N] [-m N] [-e PATTERN]... PATTERN [FILE...]",
    ),
    "gunzip": HelpEntry(
        "decompress .gz files",
        "gunzip [-k] [-f] [-c] FILE...",
    ),
    "gzcat": HelpEntry(
        "print .gz files decompressed (same as zcat)",
        "gzcat FILE...",
    ),
    "gzip": HelpEntry(
        "compress files",
        "gzip [-d] [-k] [-f] [-c] FILE...",
    ),
    "head": HelpEntry(
        "print the first lines of files",
        "head [-n N | -c N] [FILE...]",
    ),
    "help": HelpEntry(
        "list the commands, or show how one is used",
        "help [NAME]",
    ),
    "jq": HelpEntry(
        "query and transform JSON",
        "jq [-r] [-c] [-e] [-s] [-n] [-j] [-S] FILTER [FILE...]",
    ),
    "ls": HelpEntry(
        "list directory contents",
        "ls [-l] [-a] [-R] [-h] [-t | -S] [-r] [-d] [-F] [-1] [PATH...]",
    ),
    "md5sum": HelpEntry(
        "print or check MD5 checksums",
        "md5sum [-c] [FILE...]",
    ),
    "mkdir": HelpEntry(
        "make directories",
        "mkdir [-p] DIR...",
    ),
    "mv": HelpEntry(
        "move or rename files",
        "mv [-f | -n] SOURCE... DEST",
    ),
    "paste": HelpEntry(
        "join lines of files side by side",
        "paste [-s] [-d LIST] [FILE...]",
    ),
    "printf": HelpEntry(
        "print formatted text",
        "printf FORMAT [ARG...]",
    ),
    "pwd": HelpEntry(
        "print the working directory",
        "pwd",
    ),
    "realpath": HelpEntry(
        "print absolute, normalized paths (symlinks not expanded)",
        "realpath [-e | -m] [-s] PATH...",
    ),
    "rev": HelpEntry(
        "reverse each line's characters",
        "rev [FILE...]",
    ),
    "rm": HelpEntry(
        "remove files and directories",
        "rm [-r] [-f] PATH...",
    ),
    "sed": HelpEntry(
        "edit text as a stream",
        "sed [-n] [-i] [-E] (-e SCRIPT)... | SCRIPT [FILE...]",
    ),
    "seq": HelpEntry(
        "print a sequence of numbers",
        "seq [-s SEP] [-w] [FIRST [INCR]] LAST",
    ),
    "sha256sum": HelpEntry(
        "print or check SHA-256 checksums",
        "sha256sum [-c] [FILE...]",
    ),
    "sort": HelpEntry(
        "sort lines",
        "sort [-r] [-n] [-u] [-f] [-k FIELD]... [-t SEP] [FILE...]",
    ),
    "stat": HelpEntry(
        "show a file's size, type and times",
        "stat [-c FORMAT] PATH...  (FORMAT: %n %s %F %y %Y %w %W %%)",
    ),
    "tail": HelpEntry(
        "print the last lines of files",
        "tail [-n N | -n +N | -c N] [FILE...]",
    ),
    "tar": HelpEntry(
        "create, list or extract tar archives",
        "tar (-c | -x | -t) [-z] [-v] -f ARCHIVE [-C DIR] [--strip-components N] [PATH...]",
    ),
    "tee": HelpEntry(
        "copy stdin to stdout and files",
        "tee [-a] FILE...",
    ),
    "test": HelpEntry(
        "evaluate a condition",
        "test EXPRESSION",
    ),
    "touch": HelpEntry(
        "create files, or update their times",
        "touch [-c] FILE...",
    ),
    "tr": HelpEntry(
        "translate, squeeze or delete characters",
        "tr [-c] [-d] [-s] SET1 [SET2]",
    ),
    "true": HelpEntry(
        "succeed, doing nothing",
        "true",
    ),
    "uniq": HelpEntry(
        "collapse repeated adjacent lines",
        "uniq [-c] [-d | -u] [-i] [FILE]",
    ),
    "unzip": HelpEntry(
        "list or extract zip archives",
        "unzip [-l] [-o] [-d DIR] ARCHIVE",
    ),
    "wc": HelpEntry(
        "count lines, words and bytes",
        "wc [-l] [-w] [-c] [-m] [-L] [FILE...]",
    ),
    "which": HelpEntry(
        "say whether commands exist",
        "which NAME...",
    ),
    "xargs": HelpEntry(
        "run a command with arguments read from stdin",
        "xargs [-0] [-n N | -I REPLACE] [-t] COMMAND [ARG...]",
    ),
    "zcat": HelpEntry(
        "print .gz files decompressed",
        "zcat [-f] FILE...",
    ),
    "zip": HelpEntry(
        "create zip archives",
        "zip [-r] ARCHIVE PATH...",
    ),
}
