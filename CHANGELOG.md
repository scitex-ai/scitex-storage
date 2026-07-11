# Changelog

All notable changes to `scitex-storage` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

- `scan`'s directory walk now delegates to `fd` instead of Python
  `os.walk`, for multi-terabyte-scale performance. `fd` is a new
  **system** (non-PyPI) runtime dependency of `scan` only — see the
  README's "System dependencies" section — declared to the
  `scitex_dev.system_deps` federation. Missing the binary raises
  `MissingSystemDependencyError` with install instructions; there is no
  silent slow-path fallback. `scan`'s public API and read-only,
  stat-only contract are unchanged.
- New `scitex-storage find-duplicates PATH [PATH ...]` — a separate,
  explicitly opt-in verb that finds exact-duplicate files via `fclones`
  (a new system dependency of this verb only). Deliberately NOT part of
  `scan`: finding exact duplicates requires reading file contents, which
  would break `scan`'s "always safe on a nearly-full disk / network
  mount" guarantee.

## [0.1.0]

- Initial bootstrap: `scitex-storage scan [PATH ...]` MVP — a read-only,
  stat-only walk reporting the biggest space and inode (file-count)
  consumers per top-level child of a root (defaults to `~/.scitex` and
  `~/proj`). Never follows symlinked directories, never reads file
  contents, never mutates anything.
