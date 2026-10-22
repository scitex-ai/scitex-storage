---
description: |
  [TOPIC] Installing scitex-storage
  [DETAILS] Extras for CLI, GUI and docs; SDK floor; system binaries.
tags: [scitex-storage-installation]
---

# Installation

```bash
pip install scitex-storage            # CLI only (headless)
pip install scitex-storage[gui]       # + Django GUI leaf + scitex-sdk UI shell
pip install scitex-storage[dev]       # + full test suite deps
```

- GUI needs `scitex-sdk[gui]>=0.3.11` (shared `.stx-layout-fill` utility,
  AppHeader, project picker) and `django>=5.0`.
- System binaries (not PyPI, declared not bundled): `fd` (scan),
  `rsync` + ssh (archive/restore transport). Missing binaries fail loud
  with install instructions (`MissingSystemDependencyError`) — never
  silently degraded results.
- The venv contract on SciTeX nodes: reuse `/uvwork/venv-agent`
  (`/uvwork/bin/uv` for installs). No repo-local venv.
