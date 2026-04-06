# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.4] - 2026-04-06

### Fixed
- **grep**: BRE-style `\|` alternation now works — converts `\|` to ERE `|` before compilation since Python's `re` module uses ERE-like syntax. Both `grep "a\|b"` and `grep -E "a|b"` now produce the same result. `-F` (fixed strings) is unaffected.

## [0.1.3] - 2026-03-12

### Added
- **find**: `-exec {} +` batch form — accumulates matching paths and runs a single command at the end

### Fixed
- **find**: use `list_detailed()` instead of `list()` + per-file `stat()`, restoring timestamp metadata and eliminating N+1 calls
- **grep/find**: portable path handling — use `list()` relative paths with user-provided prefix to produce consistent output across FS implementations
- **grep -r / find**: preserve user-provided relative paths in output instead of normalizing to absolute
- **find -exec**: fix space-in-argument quoting for commands with quoted multi-word args
- **2>/dev/null**: treat stderr redirection as a no-op instead of erroring

## [0.1.2] - 2026-03-03

### Added
- **find**: compound predicates (`-a`, `-o`, `!`, parentheses), `-size` with units, `-exec`, `-iname`, `-print`, `-path`, `-delete`, `-empty`
- **grep**: `-e` (multiple patterns), `-m`/`--max-count`, `--exclude-dir`, `-q`/`--quiet`, `-L`/`--files-without-match`, `-H`/`--with-filename`, `-h`/`--no-filename`
- **sed**: `a` (append), `i` (insert), `c` (change), `q` (quit), `y///` (transliterate), `-E`/`-r` extended regex flag
- **diff**: `-r` (recursive directory comparison), `-U N` (configurable context lines), `-b` (ignore whitespace changes)
- **ls**: `-S` (sort by size), `-r` (reverse), `-1` (one per line), `-d` (list directory itself), `-F` (classify entries)
- **head/tail**: `-c` flag for byte count mode
- **gzip**: `-c` (write to stdout), `-1` through `-9` (compression level)
- **tar**: `--strip-components` for extraction, traditional no-dash flag form (e.g. `tar czf`)
- **cut**: `--output-delimiter`, `\t`/`\n` escape sequences in delimiter
- **wc**: `-L`/`--max-line-length`
- **touch**: `-c` (skip creating nonexistent files)
- **cp**: `-a` (archive mode, alias for `-r`)
- **mv**: `-f` (force) and `-n` (no-clobber) flags
- **jq**: `-S`/`--sort-keys`

### Fixed
- **gzip -c**: compress mode now correctly writes to stdout instead of creating a `.gz` file

### Changed
- **find -exec**: removed circular dependency by passing executor callable instead of importing `core.execute_script` inside predicate class
- Moved `grep` and `find` tests into dedicated test files

## [0.1.1] - 2026-02-28

### Fixed
- **jq last(expr)**: Uses sentinel instead of null check so null values aren't dropped
- **jq join()**: Skips null values instead of stringifying them
- **makedirs exist_ok**: Respects exist_ok=False flag
- **diff -i/-B/-w**: Shows original lines in output instead of preprocessed ones
- **sed assert**: Replaced bare assert with proper TerminalError
- **Trailing pipes**: Parser rejects trailing pipes instead of silently ignoring them
- **diff -i duplicate lines**: Fixed incorrect original line mapping when multiple lines collapse to the same preprocessed value
- **resolve_path**: Normalize paths with posixpath.normpath to handle `..` components

### Changed
- **_resolve_path**: Deduplicated into shared resolve_path helper in commands/_util.py
