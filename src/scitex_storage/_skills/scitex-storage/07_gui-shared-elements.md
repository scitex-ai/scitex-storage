---
description: |
  [TOPIC] scitex-storage GUI shared-element rules
  [DETAILS] Self-linking SDK stylesheets, adopted primitives, and what
  stays leaf-owned in the GUI.
tags: [scitex-storage-gui-shared-elements]
---

# GUI shared-element rules (scitex-sdk)

- Templates extend `scitex_sdk/app/app_shell.html`, which is only a block
  shim — the leaf links every SDK stylesheet it needs itself via
  `{% static 'scitex_sdk/ui/css/...' %}` (same pattern as `_app_header.html`
  linking `app-header.css`). Never assume the hub base page loads SDK CSS.
- Adopted shared primitives: `.stx-layout-fill` (`utils/layout.css`,
  needs scitex-sdk>=0.3.8) on organize roots instead of any leaf
  width/max-width rule (the SDK's no-`!important` contract forbids
  competing rules on the same element); SDK `AppHeader`; header project
  picker (rendered only when the host provides the tag library).
- Organize tabs are Usage / Move / Duplicates on SDK content tabs. Backup's
  GUI is removed; the independent CLI copy operation remains.
- Default All Projects is a request-local SDK view, not a global preference
  write. Explicit projects are authorized and constrain scans; invalid explicit
  projects return 403 rather than widen to user storage.
- Usage's opaque-origin sandbox permits chart scripts only. Its project query
  is preserved. Theme relay carries semantic colors, never user data.
- Logical hot/warm/cool/cold tiers resolve authorized provider volumes. Missing
  mappings remain disabled; physical archive aliases are not user tier names.
- Optional configured rates produce day/week/30-day decimal-GB estimates. Never
  invent prices. See `_logical_tiers.py` and `docs/gui-storage.md`.
- Both move endpoints are contained and authorized; project roots cannot be
  silently relocated. Confirmed moves always SHA-verify and retain originals
  for at least seven days; execution deadlines cancel actual rsync workers.
- Host-provided per-user state, no process HOME changes. A saved transaction
  phase alone does not prove worker liveness. Retention cleanup must verify the
  current destination before deleting an expired original.
- Paths are displayed through `volumes.display_path`, without altering physical
  I/O authorization. Build chart labels with text nodes, never raw HTML.
