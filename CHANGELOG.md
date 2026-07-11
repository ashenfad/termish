# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.8] - 2026-07-11

### Added
- **Stderr redirects are honored** instead of parsed-and-discarded. `2>file` captures the command's stderr (creating/truncating the file even when no stderr is produced, as in bash), `2>>file` appends, `2>/dev/null` suppresses, and `2>&1` merges stderr into stdout so it flows through pipes -- the `cmd 2>&1 | head -30` idiom now delivers the error text downstream. A failure whose stderr went to a file still aborts the pipeline (silently -- the diagnostic was consumed); a `2>&1` failure keeps the pipeline going and the last stage decides the exit code (bash without pipefail: `cat /missing 2>&1 | wc -l; echo $?` prints ` 1` then `0`). Redirect targets undergo variable expansion. The parser now emits `Redirect` nodes with types `2>`, `2>>`, `2>&1`; other fd merges (`>&1`, `1>&2`, `2>&2`) remain vacuous no-ops.
- **Stderr visibility.** The returned transcript now behaves like a terminal screen: when execution continues past a failure (`cmd; next`, `cmd && ...; next`, `cmd || rescue`), the failure's diagnostic is written into the transcript at that point. A failure with nothing executed after it still raises `TerminalError` (message unchanged, no duplication). Silent failures stay silent (`false`, or a `CommandResult` with empty stderr). A successful command's non-empty `CommandResult.stderr` (warnings) also surfaces in the transcript — never into the pipe, so `warncmd | wc -l` counts only stdout. `TerminalError` gains a `stderr` attribute carrying the failing handler's own stderr verbatim (`None` = the message is the diagnostic, `""` = silent). Previously an intermediate failure in a `;` sequence vanished entirely — a well-behaved plugin returning exit 2 + stderr produced a transcript indistinguishable from a hang.
- **xargs exit-code fidelity**: a failing sub-command's exit code and stderr now propagate through xargs's `TerminalError` (previously hardcoded to 1 with no stderr).
- **Variable expansion.** `$?` expands to the previous pipeline's exit code; `$VAR` / `${VAR}` read from an optional env dict (`execute(..., env=...)`). Expansion is execution-time and quote-aware: unquoted and double-quoted contexts expand, single quotes stay literal, `\$` escapes. Unset variables expand to empty and an unquoted arg that expands to nothing is removed (bash word removal). Expansion also applies to command names and redirect targets, and expanded values still glob (`echo $PAT` with `PAT=*.txt`). Unrecognized `$` forms (`$1`, `$$`, trailing `grep foo$` anchors) stay literal. The env dict is shared with handlers via `ctx.env` -- mutations are visible to later commands and persist across `execute()` calls when the caller reuses the dict. Closes the `cmd; echo exit=$?` gap where agents saw a literal `$?` and concluded the command hung.
- **`zcat`** / **`gzcat`** builtins -- decompress gzip files to stdout (equivalent to `gzip -dc`). Unlike `gzip -d`, no `.gz` suffix is required. Both names map to the same handler (Linux agents type `zcat`, macOS-trained ones `gzcat`). File arguments only -- piping compressed bytes via stdin is not supported (pipelines are text-based).

### Fixed
- **Command words expand as one list** (PR #15 review): an empty-expanding command name shifts away (`$UNSET echo hi` runs `echo hi`; `$UNSET` alone is a silent no-op), matching zsh. Expansions are deliberately never field-split -- zsh semantics, not bash: `CMD="echo hello"; $CMD` is a visible `command not found`, and an env value with spaces stays a single argument.
- **`\\` in double quotes** (PR #15 review): the double-quote backslash rule is now complete -- `"\\$NAME"` yields a literal backslash followed by the expansion, `"a\\b"` collapses to `a\b`, and backslashes before anything other than `\` or `$` stay untouched (`"a\.b"` regex escapes survive).

### Changed
- **Transcripts that continue past a failure now include the failure's diagnostic.** `cat /missing || true` used to return `""`; it now returns `cat: /missing: No such file or directory\n`. Callers asserting exact transcripts around rescued/ignored failures will see the new lines.
- **Command substitution `$(...)` now raises `ParseError`** ("not supported; run the inner command separately") in unquoted and double-quoted contexts instead of tokenizing into mangled args (`$`, `(`, `cmd`, `)`). Single-quoted `'$(...)'` remains a literal, as in bash.
- **Quoted command names and redirect targets are now unquoted before resolution** (`"ls" -la` works; `echo hi > "my file.txt"` writes to `my file.txt`, not a file with quotes in its name).
- **Tokenizer**: `$`, `?`, `{`, `}` are now word characters, so `$?`, `${NAME}`, and `file?.txt` stay single tokens instead of splitting.

## [0.1.7] - 2026-07-05

### Added
- **Here-documents.** `cmd <<EOF ... EOF` (and `<<'EOF'` / `<<"EOF"` — identical semantics since termish has no expansion) feed an inline body to the command's stdin. Bodies are extracted from the raw text before tokenization, so quotes, pipes, and redirects inside a body are inert. The delimiter line matches exactly or whitespace-stripped (agents indent). Multiple heredocs per line consume bodies in order; unterminated heredocs and missing delimiters raise `ParseError`. Composes with pipelines (`cat <<EOF | sort`) and `tee` for the write-a-multiline-file idiom that previously required quoting gymnastics.
- **Exit-code fidelity.** `TerminalError` gains an `exit_code` attribute (default 1, backward compatible): command-not-found raises 127, a failing `CommandResult` propagates its own code (a custom command's exit 22 now survives to the caller), and multi-pipeline scripts preserve the last failure's code alongside `partial_output`.
- **`file`** builtin -- minimal magic-byte sniffer covering gzip, zip, tar, PDF, PNG, JPEG, ELF, HTML, with a UTF-8/ASCII/binary fallback. Closes the "did my download actually produce a gzip?" sanity-check gap. Not a libmagic re-implementation -- no `--mime` / `-b` flags.
- **`true`** and **`false`** builtins -- POSIX no-ops that unblock the `cmd || true` swallow-failure idiom. Previously `cat /missing || true` failed with `true: command not found`.

### Fixed
- **Parser**: `2>&1` (and `>&1`) is now recognized as a no-op, matching the existing `2>file` behavior. Termish has no separate stderr stream during execution — handlers emit stderr only as a post-execution string on `CommandResult` — so any fd merge is vacuously a no-op. Previously the `>&` token fell through to "regular word" and `2 >& 1` became three positional args, breaking common idioms like `cmd 2>&1 | tail -20`.

## [0.1.6] - 2026-04-29

### Fixed
- **tar**: `-C dir` is now honored during archive creation (`-c`). Previously the flag was parsed but silently dropped, so `tar -czf out.tar.gz -C /tmp name` looked up `name` in the cwd instead of `/tmp` and failed with `tar: error creating archive: tar: name: No such file or directory`. File paths are now resolved under the `-C` dir while archive names stay as written; absolute file paths still win.

## [0.1.5] - 2026-04-18

### Added
- **Pluggable command injection** -- `execute()` and `execute_script()` accept an optional `commands` parameter: a mapping of name → handler that injects custom commands alongside builtins. Injected commands override builtins when names collide. Handlers receive a `CommandContext` and optionally return a `CommandResult` for exit code / stderr signaling.
- **`CommandContext`** and **`CommandResult`** types (`termish/context.py`) -- unified input/output for all command handlers. Both builtin and injected commands use the same signature.
- **`CommandFunc`** type alias updated to `Callable[[CommandContext], CommandResult | None]`.
- New exports: `CommandContext`, `CommandResult`, `CommandFunc`.

### Changed
- **All 30 builtin commands refactored** to use `CommandContext` signature internally (from `(args, stdin, stdout, fs)`). The public API (`execute()`, `execute_script()`) is unchanged — existing callers work without modification.
- **`xargs`** now resolves injected commands via `_resolve_command()` (not just builtins). Propagates `ctx.env` to sub-commands and wraps generic exceptions.
- **`gunzip`** uses `dataclasses.replace(ctx, ...)` for forward-compatible context propagation.
- Nested `execute()` / `execute_script()` calls inherit the parent's injected commands when `commands=None`.

### Fixed
- **Parser**: `:`, `@`, `,`, `%`, `+`, `!`, `^` added to `shlex.wordchars` so tokens like `user@host`, `100%`, `foo:bar` are not incorrectly split.

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
