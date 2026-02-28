# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
