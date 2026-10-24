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
- Organize tabs (Usage / Duplicates / Move / Backup) are server-rendered
  links on the shared SDK `stx-content-tabs` primitive
  (scitex-sdk>=0.3.9, underline-only selected via `aria-selected`): a
  click loads a new page, no JS pane producer, plus one anchor adapter
  resetting UA underline. The old `.stx-storage-tabs` variant is deleted.
  All four tabs are real views in `organize.py` (`HANDLED_TABS`); Backup
  is plan-form plus recorded-manifest list (plan-only — the CLI
  `backup --yes` gate stays its only mutator). Move adds an explicit-confirm
  apply form posting to `move-apply/` (bounded run, edit rights enforced
  for project scope, staged restorable instead of deleted).
- Organize tabs are scope-aware through the shared app header (no in-tab
  scope toggle by design): project scope scans/resolves within the active
  `ProjectScope` (fail-closed `None` renders an explicit no-project
  notice, never a silent fallback); move planning additionally confines
  the source inside the project dir (double containment).
- The Move plan display names the delete gate (count-and-size read-back by
  default, `--verify-content` for the independent re-hash) — same honesty
  as the CLI "delete cleared by" line; the applied card names the
  restorable staging instead of a delete.
