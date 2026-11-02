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


The Usage center regression is a real browser integration test:

```bash
pytest tests/integration/scitex_storage/_render/test_sunburst_center_real.py
```

It uses temporary files and the real directory-tree renderer, native mouse
and touch at fourteen disk points, keyboard parent/root controls, ring/breadcrumb
navigation and malformed/traversal-shaped hashes. Only already-loaded nodes
may be visited; no ancestor URL or filesystem request is permitted. Screenshots
remain in that run's temporary directory. These fixture screenshots are not a
claim about a deployed page. Browser availability follows the fixture above.

`tests/scitex_storage/_django/test_organize.py` exercises the production SDK
provider path (no test-only project seam), encoded/absolute foreign selectors,
ignored free-form path queries, per-project narrowing, and escaping directory
symlinks. Its roots and file contents are temporary fixtures, and every request
checks those contents remain unchanged. The tests prove existing protections;
they do not add an unrestricted path or parent-navigation API.

`test_sunburst_touch_iframe.py` retains the observed phone iframe dimensions and
fractional SVG translation. A native touch inside radius 70 must navigate to
the loaded parent even after the browser generates its compatibility click.
The center is painted after arcs; its radius and ring bounds stay unchanged.
