# Changelog

All notable changes to `scitex-storage` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

- Render the Usage parent without duplicating the folder scan performed by
  its separately loaded sunburst; show deferred details instead of a premature
  zero-byte total. Preserve chart scope checks and existing scan deadlines.

- Reject colliding or occupied reclaim destinations before any source moves,
  including older plans; publish same-filesystem moves without overwriting a
  destination that appeared after the preview.
