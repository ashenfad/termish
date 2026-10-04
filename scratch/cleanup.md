# termish cleanup notes

Captured from a cursory conceptual review on 2026-09-06 (termish 0.1.9 +
unreleased find -exec fix). Docs, changelog, interpreter core, protocol,
and a sample of the builtins; three claims reproduced.

## Summary

Solid, well-tested (669 tests), and shaped by watching agents fail
against it. One architectural ceiling (text-only pipelines) should be
decided deliberately before more builtins land. The rest is API shape.

## 1. Pipelines are text; binary silently corrupts (main finding)

`CommandContext.stdin` / `stdout` are `TextIO`, pipe content is `str`,
and file reads decode with `errors="replace"`. Consequences:

- `cat bin.dat > copy.dat` on a 256-byte file holding every byte value
  writes a 512-byte copy: each high byte becomes the 3-byte U+FFFD
  sequence. No error, no warning. *(reproduced)*
- `cat file.gz | zcat` cannot work ("zcat: no files specified"); every
  archive builtin reads files directly rather than composing.
  *(reproduced)*
- Any binary tool an agent expects to pipe through (`gunzip -c`,
  `base64`, `head -c`, a `curl` builtin returning binary) hits a wall
  it cannot see.

Fix direction: bytes on the wire, text decoding at each builtin's
boundary. Touches every builtin, so it is a project, not a patch.

Interim: make the loss visible. Raise (or at least warn in the
transcript) when a redirect or pipe would carry undecodable bytes, so
`cat bin > copy` fails loudly instead of corrupting.

## 2. Stderr is a post-hoc string, which forces the routing code

Handlers return stderr on `CommandResult` after the fact. The
interpreter then reconstructs where it should have gone: pending
diagnostics, partial-output flushing on abort, three stderr redirect
types, merged-failure bookkeeping (~100 lines in `_execute_pipeline`).

A `ctx.stderr: TextIO` in `CommandContext`, with `CommandResult.stderr`
kept for compatibility, lets the router pick the destination once and
makes interleaving order faithful by construction. Medium refactor;
builtins can migrate incrementally.

## 3. Raising on failure is the least shell-like part of the API

A shell returns `$?`. `execute()` raises `TerminalError` when the last
pipeline fails, so the primary consumer (an agent harness) catches it
and reassembles the transcript from `partial_output` + diagnostic. An
`ExecResult(output, exit_code, stderr)` return matches what every caller
does anyway. Low urgency; agex may already wrap this. Could ship as a
new entry point beside `execute()` to avoid a breaking change.

## 4. Control flow is absent; the failure mode is noisy, not clear

`for f in a b; do echo $f; done` yields three separate
`command not found` errors (`for`, `do`, `done`). *(reproduced)*
Visible, which beats silent mangling, but `$(...)` got a dedicated
`ParseError` that explains itself; `for` / `while` / `if` / `case`
deserve the same. Whether to implement loops is a separate question
(xargs + `find -exec` cover most real uses).

## 5. Verified good

- xargs and `find -exec` both dispatch argv directly via
  `_resolve_command`; no string rebuilding, so the injection class
  fixed in the unreleased entry is closed everywhere.
- No field splitting (zsh semantics) is well argued and right for
  agent-typed input.
- Heredoc bodies are extracted before tokenization; quoting inside is
  inert.
- Injected commands via contextvar so nested dispatch (xargs, find)
  sees them without parameter threading.

## 6. Smaller

- `except Exception` in the script loop wraps builtin bugs as
  "Unexpected error" in the transcript. Right for the agent, but hides
  library bugs from the developer; consider logging at WARNING.
- `env` is threaded explicitly while `commands` ride a contextvar.
  Harmless inconsistency.
- jq engine (~1650 lines) is a partial reimplementation. Agents know
  full jq; expect a long tail of unsupported-filter reports. Fine as
  long as every gap errors visibly.

## Ecosystem note (for brainstorming)

termish and monkeyfs each define their own `FileSystem` protocol,
`FileMetadata`, and `FileInfo`. They overlap on ~10 methods but differ
in shape (termish frozen dataclasses; monkeyfs mutable with
`stat_result` properties). `VirtualFS` satisfies both by having ~40
methods; the `_collect_files` comment in `search.py` about
`list_detailed().path` varying across implementations is the drift
showing. A shared tiny protocol package is the obvious answer; termish's
zero-dependency stance is the constraint.

## Suggested order

1. Undecodable-bytes guard on pipes and redirects (small, stops silent
   corruption today).
2. Dedicated ParseError for `for` / `while` / `if` / `case` (small).
3. `ctx.stderr` stream (medium; simplifies core).
4. Bytes-on-the-wire pipelines (large; do before adding more builtins).
5. `ExecResult` entry point (small, additive).

## A command-not-found hook (found 2026-09-11)

termish raises `{cmd}: command not found` (exit 127) with no seam for
an embedder to answer. nontainer wants to hint `pytest` / `vitest` /
`npx vitest` / `npm test` toward its `ws-pytest` / `ws-vitest` verbs
without registering those four public names as commands. A small
`not_found` handler (or a suggestions map) on the shell gives that; on
dud the guest's real binary would run first, so the hint is a
local-rung nicety, documented as such.
