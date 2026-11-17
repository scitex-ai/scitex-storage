# Changelog

All notable changes to `scitex-storage` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

- Preflight all reclaim-restore entries before moving any, including missing
  archives and colliding/occupied destinations; publish same-filesystem
  restores with an atomic no-overwrite rename.

- Refuse reclaim run IDs that escape archive/manifest roots, including legacy
  caller-created plans; refuse restore when a manifest records another run ID.

- Correct reclaim documentation/help to distinguish completed copy/restore
  from independent verification, resumability and crash durability; add real
  separate-filesystem file/directory round-trip coverage.

- Refuse ambiguous relative remote archive/backup paths before transport or
  verification; require an explicit absolute path or `~/path`.

- Render the Usage parent without duplicating the folder scan performed by
  its separately loaded sunburst; show deferred details instead of a premature
  zero-byte total. Preserve chart scope checks and existing scan deadlines.

- Preserve files and symlinks named by a directory-only backup exclude;
  count and content baselines now distinguish real directories as rsync does.

- Clear the previous sunburst tooltip when navigating between hierarchy levels.
- Capture the verified root after native navigation in browser regression tests,
  retaining the required screenshot and all boundary assertions.
