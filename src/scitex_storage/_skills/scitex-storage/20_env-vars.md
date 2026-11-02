---
description: |
  [TOPIC] scitex-storage environment variables
  [DETAILS] Every SCITEX_STORAGE_* / SCITEX_* knob with source and default.
tags: [scitex-storage-env-vars]
---

# Environment variables

| Variable | Read in | Meaning |
|---|---|---|
| `SCITEX_STORAGE_VOLUMES_PROVIDER` | `_django/volumes.py` | dotted path to host callable returning the requester's volumes; absent + non-standalone = fail closed (no volumes) |
| `SCITEX_APP_MODE` | `_django/volumes.py` | `standalone` (default) serves `Path.home()`; any other value without a provider yields no volumes |
| `SCITEX_DIR` | `_observe`, `_cli/_completion.py` | state root, default `~/.scitex` (sandbox in tests) |
| `SCITEX_STORAGE_SKILLS_DEST` | `_cli/_skills_cmd.py` | install destination for `skills install` |
| `SCITEX_PROJECT_PROVIDER` / `SCITEX_PROJECT_STORAGE` | `_django/project_files.py` (Django settings keys) | hub-registered project scope + storage capabilities; absent = fail closed (`no_project` 404) |
