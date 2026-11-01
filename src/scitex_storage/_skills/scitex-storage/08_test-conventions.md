---
description: |
  [TOPIC] scitex-storage test conventions
  [DETAILS] TDD cycle, AAA markers, PS-204 mirror rule, single-assert
  rule and worktree test invocation.
tags: [scitex-storage-test-conventions]
---

# Test conventions

- TDD: failing test first, watched fail for the right reason, minimal code,
  full suite after. AAA markers (`# Arrange/# Act/# Assert`).
- PS-204 mirror rule: every test file mirrors a src module
  (`test_views.py` ↔ `views.py`, `test__foo.py` ↔ `_foo.py`). No orphan
  test files — find the compliant home instead.
- STX-TQ007: one assertion per test function (helper asserts count too —
  helpers raise, tests assert).
- GUI tests use the real Django request factory against real temp dirs and
  assert on the real rendered body and repo files, never constants.
- Audit (`audit-all`, exit 0) is part of green, alongside the suite.
- When the venv's editable install points at another worktree, run with
  `PYTHONPATH=<worktree>/src` so tests import the tree under test.

The sunburst label security regression uses real Chromium, temporary files,
pointer hover, and zoom. Install the dev extra and a Playwright Chromium binary,
then run:

```bash
python -m playwright install chromium
pytest tests/scitex_storage/_render/test__sunburst.py -k filesystem_labels
```

An existing Chromium binary can be selected with the test-only
`SCITEX_STORAGE_TEST_CHROMIUM` environment variable. Without a browser this
integration test reports a skip; that is not browser coverage. Its temporary
directory retains `sunburst-labels.png` for visual inspection.
