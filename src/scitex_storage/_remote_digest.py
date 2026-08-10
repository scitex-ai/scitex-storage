#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# File: src/scitex_storage/_remote_digest.py
"""Hash a tree on the FAR side of an ssh connection.

WHY THIS IS NEEDED AND WAS NOT OBVIOUS. ``_content_verify.digest_tree`` walks a
LOCAL path. The one place in this package that actually deletes something --
``apply_archive`` -- has a REMOTE destination, so the strict content gate could
not reach the code that most needs it. That gap is real and shipped: a
count-and-size verdict licenses ``shutil.rmtree(source)`` today.

THE NAS UNITS RUN BUSYBOX, AND THAT SHAPES EVERY LINE OF THE COMMAND BELOW.
Measured 2026-07-29 on nas2: ``pgrep`` does not exist there, and its absence
returned empty output that read exactly like a legitimate count of zero. So:

* ``find -printf`` is a GNU extension. BusyBox find does not have it, and the
  failure is a usage error, not a clean empty result.
* ``sha256sum`` exists but its output column layout is the only thing we can
  rely on -- take the first 64 characters, not ``awk '{print $1}'``, because a
  filename with spaces is normal and the hash column is fixed-width.
* ``readlink`` exists; ``readlink -f`` semantics differ. We only ever want the
  raw target string, so plain ``readlink`` is both sufficient and portable.

AN EMPTY ANSWER IS NEVER AN EMPTY TREE. The command emits an explicit marker
when it cannot cd into the root, and :func:`parse_remote_manifest` refuses to
build a manifest from empty stdout. That refusal is the whole point: an ssh
that connected, ran nothing useful and exited 0 produces exactly the same
bytes as a genuinely empty directory, and one of those readings would license
deleting the source. Same shape as the unmounted-mount-point guard in
``_survey`` and the zero-entry guard in ``_content_verify``.

SYMLINKS DIGEST THEIR TARGET STRING, matching ``digest_file`` exactly -- the
two sides must measure the same thing or every symlink is a false mismatch.
"""

from __future__ import annotations

import hashlib

from ._content_verify import SYMLINK_PREFIX, ContentManifest

#: Emitted when the remote root cannot be entered. A distinct token rather
#: than empty output, so "not there" and "nothing to say" stay separable.
MISSING_ROOT_MARKER = "SCITEX_REMOTE_DIGEST_MISSING_ROOT"

#: Prefixes an entry the remote could not hash. Kept as its own population
#: rather than dropped: a dropped unreadable file is silently equivalent to a
#: matching one, and the difference is exactly the file you would delete.
UNREADABLE_MARKER = "UNREADABLE"

#: POSIX-sh, BusyBox-safe. `{path}` is substituted with an already-quoted path.
#:
#: Deliberately NOT a one-liner pipeline into `xargs sha256sum`: xargs
#: splitting on whitespace mangles ordinary filenames, and the failure is a
#: WRONG hash for a DIFFERENT file rather than an error.
REMOTE_DIGEST_CMD = (
    "cd {path} 2>/dev/null || {{ echo '" + MISSING_ROOT_MARKER + "'; exit 0; }}; "
    "find . ! -type d | while IFS= read -r f; do "
    'if [ -L "$f" ]; then '
    "printf 'symlink:%s' \"$(readlink \"$f\")\" | sha256sum 2>/dev/null "
    "| cut -c1-64 | tr -d '\\n'; printf ' %s\\n' \"$f\"; "
    "else "
    'h=$(sha256sum "$f" 2>/dev/null | cut -c1-64); '
    'if [ -z "$h" ]; then printf "' + UNREADABLE_MARKER + ' %s\\n" "$f"; '
    'else printf "%s %s\\n" "$h" "$f"; fi; '
    "fi; done"
)


def local_symlink_digest(target: str) -> str:
    """The digest the remote command produces for a symlink to ``target``.

    Exists so a test can assert both sides agree WITHOUT shelling out, and so
    the contract between this module and ``_content_verify.digest_file`` is
    written down in one place rather than duplicated in two shell strings.
    """
    return hashlib.sha256(
        (SYMLINK_PREFIX + target).encode("utf-8", "surrogateescape")
    ).hexdigest()


def parse_remote_manifest(stdout: str, *, probe_succeeded: bool = True) -> ContentManifest:
    """Turn :data:`REMOTE_DIGEST_CMD` output into a :class:`ContentManifest`.

    ``probe_succeeded`` is the ssh-level outcome, and it is a SEPARATE fact
    from the command's output. An ssh that failed to connect produces empty
    stdout, which is byte-identical to a command that ran fine over an empty
    directory. The caller knows which happened; this function must be told
    rather than guess.
    """
    if not probe_succeeded:
        return ContentManifest(
            digests={},
            unreadable={"<probe>": "the remote probe did not run to completion"},
        )

    lines = [ln for ln in stdout.splitlines() if ln.strip()]

    if any(ln.strip() == MISSING_ROOT_MARKER for ln in lines):
        return ContentManifest(root_missing=True)

    if not lines:
        # NOT an empty tree. A connected-but-silent probe and a genuinely
        # empty directory are the same zero bytes, and only one of them is a
        # measurement. `verify_content` turns an unreadable entry into
        # COULD_NOT_LOOK, which is the verdict this deserves.
        return ContentManifest(
            digests={},
            unreadable={
                "<probe>": (
                    "the remote command produced NO output at all -- this is "
                    "not an empty tree, it is an unanswered question, and the "
                    "two are indistinguishable from here"
                )
            },
        )

    digests: dict[str, str] = {}
    unreadable: dict[str, str] = {}

    for line in lines:
        marker, _, rest = line.partition(" ")
        rel = rest.strip()
        if rel.startswith("./"):
            rel = rel[2:]
        if not rel:
            unreadable[f"<unparseable:{line[:60]}>"] = "no path field in this line"
            continue
        if marker == UNREADABLE_MARKER:
            unreadable[rel] = "the remote could not hash this entry"
        elif len(marker) == 64 and all(c in "0123456789abcdef" for c in marker):
            digests[rel] = marker
        else:
            # A line that is neither a hash nor the declared marker means the
            # remote emitted something nobody planned for. Recording it as
            # unreadable poisons the verdict, which is correct: we do not know
            # what that entry is, and guessing is how a delete gets licensed.
            unreadable[rel] = f"unrecognised digest field {marker[:32]!r}"

    return ContentManifest(digests=digests, unreadable=unreadable)

# EOF
