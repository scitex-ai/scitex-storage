# Changelog

All notable changes to `scitex-storage` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

- Correct reclaim documentation/help to distinguish completed copy/restore
  from independent verification, resumability and crash durability; add real
  separate-filesystem file/directory round-trip coverage.

- Render the Usage parent without duplicating the folder scan performed by
  its separately loaded sunburst; show deferred details instead of a premature
  zero-byte total. Preserve chart scope checks and existing scan deadlines.

- Preserve files and symlinks named by a directory-only backup exclude;
  count and content baselines now distinguish real directories as rsync does.

- Clear the previous sunburst tooltip when navigating between hierarchy levels.
- Capture the verified root after native navigation in browser regression tests,
  retaining the required screenshot and all boundary assertions.
