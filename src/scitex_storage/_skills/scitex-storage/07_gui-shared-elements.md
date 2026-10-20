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
- Tabs (`?tab=` server-rendered links) stay leaf-owned until the SDK
  content-tabs standard is consumable AND adoption guidance exists —
  converging without guidance is guessing.
- The Move plan display names the delete gate (count-and-size read-back by
  default, `--verify-content` for the independent re-hash) — same honesty
  as the CLI "delete cleared by" line.
