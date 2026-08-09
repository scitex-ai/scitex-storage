# Changelog

All notable changes to `scitex-storage` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **`scitex-storage alarm` — a storage alarm that PUSHES, because a dashboard
  is for a reader who is already looking.** On 2026-08-09 `scitex-compute-04`
  reached **364 MB free on a 393 GB volume** and nothing reported it. It
  surfaced only because a routine `head` inside an unrelated five-minute cron
  on another agent happened to write and died with `ENOSPC` — the detection
  mechanism was *"an agent happens to run a command that writes"*. The next
  occurrence corrupts a SQLite mid-transaction rather than killing a text
  filter, and that host carries the fleet's agent state DB.
  - **The measurement layer already existed and needed nothing.** `_observe`
    gathers the fleet, `HostStorage` carries space and inode percentages with
    a three-state verdict, `FLAG_PERCENT` already decides what renders red.
    The gap was that `write_fleet_snapshot` renders HTML and stops. So this is
    one layer — decide, render a sentence, deliver it — not a new monitor.
  - **`FLAG_PERCENT` is reused, never redefined.** Two thresholds for one
    concept drift apart, and then the dashboard and the alarm disagree about
    whether the fleet is healthy.
  - **Absolute floors (20 GiB warn / 5 GiB critical) sit alongside the
    percentage**, because a percentage cannot answer *how long have I got*.
    Free space on that volume fell ~2 GB per five minutes; a floor stated in
    bytes is directly comparable against an observed fill rate.
  - **Inodes get both threshold families.** The original request was
    bytes-only, and a byte-only alarm is blind to the exhaustion that fails
    writes while `df` still shows free space.
  - **`UNKNOWN` is a level, not a silence.** An unmeasurable filesystem *and*
    an empty gather both report `UNKNOWN`, never `OK`, counted apart from
    healthy rows — but neither pages on its own, because alarming on a
    transient unreachable host trains the reader to ignore the channel, which
    is the original defect arriving later wearing a different hat.
  - **Dispatch is not delivery.** `PushResult.delivered` is three-valued, and
    the default operator-DM rail returns *unknown* on success rather than
    *true*: the store hands back a stored row, which proves the message was
    written, not read.
  - **Sustained blindness is announced once.** `ok → unknown → unknown → …`
    would otherwise be silent forever, and a filesystem nobody can read is not
    healthy — it is unmonitored, which is this feature's own defect in a
    narrower form. It gets its own sentence ("no reading from X for N
    gathers … only unread"), never a capacity claim, because *"we cannot see
    it"* and *"it is full"* call for different actions from different people.
  - Found in review by `scitex-db`, who also reported the original incident.

## [0.3.1] - 2026-07-29

Three defects that shipped in 0.3.0. Two are the same class — a probe that
could not see reported a confident answer instead of refusing — and the
third is its cousin: a declaration that was correct in prose and wrong in
the data a program actually reads.

### Fixed

- **`fclones` was declared as an apt package, and it is not one.** The
  system-deps federation is apt-shaped by construction (`SystemDepSpec`
  carries `package`, `purpose`, `provider`, `apt_repo`, and `apt_repo`
  means "an extra apt *source* is needed first", not "this does not come
  from apt"). `fclones` ships via cargo and GitHub releases; Debian and
  Ubuntu have no such package. The declaration carried a prose caveat
  saying exactly this, prominently, three times in the module — and when
  scitex-storage was first installed into a fleet image layer, the
  aggregator fed `fclones` to `apt-get install` anyway, because **a
  consumer reads the list, not the prose around it.**
  - **The blast radius is the instructive part:** apt aborts the *entire*
    transaction on one unknown name, so biber, chktex, latexmk and every
    texlive package silently did not install either. The build then failed
    four lines later on `pdflatex: not found` — an error pointing at a
    layer nobody had changed. One wrong entry took down twenty right ones
    and disguised itself as someone else's problem.
  - The requirement **moves rather than disappears**: `NON_APT_REQUIREMENTS`
    carries the binary, its purpose and its install method as data.
    Deleting the declaration outright would have bought a working build by
    making the aggregate lie by omission — trading a loud failure for a
    silent gap, which is the worse deal.
  - A stopgap, not a design. Until the spec can express a channel, every
    leaf with a non-apt tool faces the same forced choice between
    detonating an apt transaction and misdeclaring its dependencies.

- **`survey` called a tree MOVABLE when it read ZERO files — the wrong
  answer in the direction that loses data.** The coverage signal returned
  `movable` whenever the walk completed without error, on the evidence
  "the walk read every entry it encountered (0 files)". A count of zero
  over an empty denominator is not a clean result; it is no result wearing
  a clean result's clothes. **An unmounted mount point is a readable,
  error-free, empty directory** — so is a tree whose contents live on a NAS
  that is not currently attached. Both produce exactly that state, and the
  classifier said "safe to move" *precisely when the data was invisible
  rather than absent*. For a package that manages three NAS units and whose
  consumer is a cleanup sweep, this is the one wrong answer that costs
  something irreversible. Now returns `could-not-look` with evidence naming
  the ambiguity and the next step (confirm the filesystem is mounted),
  because a refusal that does not say what to check is a dead end.
  Verified by mutation: removing the guard makes the test fail with
  `assert 'movable' == 'could-not-look'`.

- **One stale sibling dependency disabled every CLI verb.** Measured in
  production by scitex-hpc inside the real solver image on a Spartan
  compute node: the image baked `scitex_ssh` 1.0.1 while this package
  requires `>=1.2.0`, so `sync_dir` was missing. `_cli/__init__.py`
  imported the archive verb eagerly, which pulled `_archive`, which
  imported `sync_dir` at module scope — and `survey` and `find-recipe`,
  **neither of which uses SSH**, died before argparse ever saw the
  subcommand. Verbs now load on demand from a registry.
  - It exited **1**. This package's exit codes exist so a broken verb
    cannot impersonate an answer (`find-recipe` owns 10/11, `survey` owns
    12/13, disjoint), and the failure arrived from *upstream* of the code
    that owns that contract, falling through to the shell's generic 1. A
    verb that cannot import now exits **20** — reserved for VERB
    UNAVAILABLE, outside every verdict range, because "the tool is broken"
    and "the answer is unknown" are different facts.
  - `--help` and completion answer from the registry **without importing
    anything**, so the CLI can still say what exists at exactly the moment
    its dependencies are broken.

- **The `find-recipe` help examples shipped un-runnable in 0.3.0** —
  `find-recipe/path/to/...` and `find-recipeENV`, both missing a space, so
  copy-pasting either fails. The `regenerable` → `find-recipe` rename was
  applied as a substitution that consumed the trailing space. The rename
  *was* verified — against the JSON keys and the exit codes, the half a
  machine reads. Nothing checked the half a human copies, which is where a
  new consumer starts. Now pinned by parsing the rendered help the way a
  shell would.

### Added

- **`__pycache__` holding at least one `.pyc` is a `cache`.** Replacing
  the consumer's directory-NAME list with structural detection was right,
  but it lost this case — a real regression they reported rather than
  worked around. Admitted on a distinction that keeps it from being a
  slippery slope: **a name a tool MANDATES is not a name a human CHOSE.**
  `venv`/`mamba`/`pylibs`/`rsandbox` are arbitrary labels for one kind of
  tree, which is exactly why a name list matched almost nothing; CPython
  writes `__pycache__` by language specification, so it is a protocol
  constant that happens to be spelled as a directory. The name alone is
  still never enough — content must corroborate, so a directory a human
  named `__pycache__` and filled with real data is not cleared.
- **A test pinning the `.cache` / `.pip` / `.hf` carve-out as a decision**,
  not an oversight. Measured 2026-07-28: `~/.cache/uv` writes
  `CACHEDIR.TAG` and is already caught; `~/.cache/pip` and
  `~/.cache/huggingface` do not. Those paths are redirectable via
  `XDG_CACHE_HOME` / `PIP_CACHE_DIR` / `HF_HOME`, so the name is a guess
  about a directory rather than a fact about it. They stay
  `not-regenerable` and are kept — under-reclaiming knowingly, because a
  rule that cannot be fooled is worth more than one that catches more.
  The sample was taken in a container rather than on the Spartan capsule
  trees that decide the payoff, so the `CACHEDIR.TAG` findings are treated
  as local-only; only the `__pycache__` rule rests on a specification that
  generalises.

## [0.3.0] - 2026-07-28

### The movability classifier — Layer 1, complete

Eight mechanical signals, each encoding a specific way the 2026-07-22
ywata-note-win incident's reasoning went wrong. That night five reclaim
candidates were proposed and all five were withdrawn under measurement,
while the real 681 GB sat in a directory nobody could read. These are
those corrections as code rather than as a checklist someone remembers.

- **S1 coldness** pairs `mtime` **with** `atime`. A READER LEAVES NO
  MTIME: a corpus read daily is byte-identical to an abandoned one under
  an mtime-only probe. A 187 GiB proposal was withdrawn when atime showed
  the tree had been read 11 hours earlier.
- **S2 open-handle check** (`/proc/<pid>/fd` **and** `maps`, so an mmap'd
  file counts as a holder) — the only signal that answers "is anything
  standing on this RIGHT NOW". It **requires a positive control** and
  returns `could-not-look` without one: an empty `/proc` scan and a blind
  one are indistinguishable, and the blind one produces the answer you
  were hoping for. Its verdict states its own coverage limit — other
  users, other namespaces and remote clients are not enumerated.
- **S3 destination reality** — the path must appear in `/proc/mounts` and
  its fsid must differ from the source's. `/mnt/nas2` was a bare
  directory on the dying root filesystem; writing "to the NAS" would have
  poured data onto the disk being relieved while every signal reported
  success.
- **S4 two-sided free-space preflight** — an estimate with no destination
  probe passes on a full disk; a destination probe with no estimate
  cannot say whether what is free is *enough*.
- **S5 accounting audit** — `sum(measured)` vs `df`. IF MEASURED <<
  REPORTED, SUSPECT SCOPE BEFORE PRECISION. It **refuses** rather than
  warns while a residual is unexplained, because the failure being
  prevented is continuing to reason confidently inside a scope that does
  not contain the answer, and a warning is what a confident reasoner
  walks past. An allowance requires a *written* reason.
- **S6 timestamp clustering** — N items sharing a timestamp are ONE
  event, not N facts. 37 overlays "written 0.3d ago" was a single hook
  push.
- **S7 duplicate detection** as a signal — the only class that frees
  space at zero risk and zero loss. It never returns `not-movable`: a
  duplicate is not a holder, and treating one as an obstacle would block
  the safest reclaim there is.
- **S8 permission-stub detection** — a `du` result equal to a
  directory's own size with no readable children is a *could-not-look*,
  not an empty directory. That is exactly how the 681 GB hid.

`combine()` is deliberately **not a vote**: any `could-not-look` poisons
the verdict, and one "something is standing on this" outranks any number
of agreements. A `Signal` refuses to exist without evidence.

### Regenerability — a separate axis from movability

"Can this move?" and "does deleting this lose anything?" are different
questions. `_regenerable` answers the second, **structurally**: detection
keys on `pyvenv.cfg`, `conda-meta`, `renv/library`, `site-packages` and
top-level `*.dist-info/` — never on a directory's name. Eight distinct
environment directory names were observed in one real project
(`rsandbox`, `mamba`, `pylibs`, `myenv`, `mmroot`, `mm_root`, `venv`,
`renv`), and one directory named `renv` turned out to be a full conda
prefix. A name-based rule under-matches and leaves the quota unmoved,
which is the failure that *looks* like success.

- A tree counts as regenerable **only if the recipe that rebuilds it
  still exists**. A `site-packages` with no spec above it is the only
  copy, not a cache. The validator refuses to construct that verdict
  without naming the spec file.
- **`cache`** is a third class, added after real capsule trees showed the
  largest consumers were neither environments nor non-environments. A
  CACHE IS REGENERABLE WITH NO SPEC, BECAUSE THE RECIPE IS THE NETWORK.
  Granted only by a marker the *writing tool* chose — `CACHEDIR.TAG`, or
  a conda root holding only `pkgs/` — never by absence of evidence.
- `extra_spec_paths` lets a caller **name** a recipe an ancestor walk
  cannot reach (a sibling corpus). Only *discovery* is relaxed: each
  named path is verified on disk, a directory does not count, the first
  *existing* candidate wins, a discovered recipe still beats a supplied
  one, and the verdict records which it was.
- `could-not-look` now carries a `reason` — `not-a-directory` / `absent`
  / `unreadable`. A file is routine and skippable; an ABSENT path means
  the caller's inventory is stale. A deletion sweep must treat those
  oppositely.

### `find-recipe` — the detector, reachable

New `scitex-storage find-recipe PATH [--stop-at DIR] [--spec PATH ...]
[--json]`. A Python API is not a capability for a bash caller in another
container; this verb is the contract across that boundary.

Fixed JSON shape (every key present, `null` where inapplicable), and
**declared exit codes that a missing binary cannot forge**: `0`
regenerable/cache, `10` not-regenerable, `11` could-not-look. `1` and `2`
already mean generic-failure and usage-error everywhere, so spending them
on domain meanings lets "not installed" impersonate a verdict — measured
the day this shipped. The lazy reading (`if cmd; then rm; fi`) is also
the safe one, since a cache is disposable by definition.

The verb never deletes anything. Whether something *may* be deleted is a
policy decision for the caller, who knows what the record consists of.

### Safer moves

- **`archive` reads the destination back before removing the original.**
  Its docstring had promised "push, verify, write manifest, THEN remove
  source" while the body verified nothing — rsync's exit code says the
  transfer it attempted succeeded, not that the destination holds what
  the source held. A mismatch *or* an unanswerable probe raises
  `ArchiveNotVerifiedError`, leaves the source intact, and records the
  verdict in the manifest so a refusal is auditable.
  The baseline is measured with the **same semantics on both sides** — a
  count drawn from the inode model excludes symlinks-to-directories that
  `rsync -a` writes, which produced a 20,492-member false alarm once.
  A *surplus* is disqualifying too: looking better than expected means
  the baseline is wrong.
- **`archive` and `reclaim` now measure the destination before moving
  data.** A real `df -Pk` probe over ssh (POSIX-portable, because the NAS
  units run BusyBox), returning `None` rather than `0` when unparseable —
  zero is a measurement meaning "full". "The NAS is roomy" is a
  capability claim, and a capability claim is a measurement.
- **`sweep` refuses rather than filling the disk it was called to
  relieve**, and its help now says so: it writes the tar beside the
  source, so it needs free space to free space, and is unusable on a
  filesystem already out of room — which is exactly when someone reaches
  for a cleanup tool. The help names what to use instead.

### Fleet observation

- Multi-host `observe` via scitex-dev's host registry (no second host
  list), with a `redundancy` model that ships **no default policy** — the
  caller supplies the classes and RPO/RTO, and `assess_all` raises rather
  than inventing a standard and then reporting compliance against it.
- Live-run fixes the unit tests could not catch, because both were
  written from the same GNU output the code assumed: macOS `df -Pi`
  emits nine columns against GNU's six (every Mac filesystem reported
  "inodes unavailable"), and `df` exits non-zero when *any one* mount is
  unreadable, so a single stale share made two whole hosts report
  could-not-look. Partial success is success.
- The GUI's bare-Django fallback is loud instead of silent.

## [Unreleased]

- New `scitex-storage reclaim PATH...` / `reclaim-restore RUN_ID` — move a
  path **aside into a reversible archive instead of deleting it**. This is
  the operator's archive-instead-of-delete rule as a mechanism: because a
  wrong call costs a `reclaim-restore` rather than lost data, a cleanup
  decision is allowed to be *rough*, which is what lets it ship before its
  classifier is perfect.

  By default the archive is an adjacent `.old/<timestamp>/` beside each
  source — same filesystem, so the move is an instant atomic rename. That
  tidies a tree but does **not** free the source filesystem's inodes/space
  (the files are merely relocated). Pass `--archive-root` at a *different*
  filesystem to actually reclaim inodes/space; that move is a verified
  copy-then-delete, not atomic, and is checked before the source is removed.
  One mechanism, two jobs — the caller states the destination, because "free
  this filesystem" and "tidy this directory" are different intents.

  `reclaim --status` reports every run and the **restore rate** — the
  fraction of runs later pulled back out — which is the honest accuracy
  metric for whatever chose the paths, measured rather than guessed. No
  data reports as `n/a`, never a reassuring `0%`. Deleting archived data is
  a separate, later step; this verb never unlinks anything. Local-only, no
  `rsync`/network/`fd`, so it is testable end-to-end over real temp dirs.
  Defaults to a dry-run.

- `archive` / `restore` now **declare and check `rsync`**, the transport they
  were always built on. Both delegate to scitex-ssh's `sync_dir` — "a thin,
  policy-free wrapper over `rsync -a`" — so the local `rsync` binary is as
  hard a runtime dependency of `archive` as `fd` is of `scan`. It was
  declared nowhere and is absent from the container this package ships to,
  so `archive` could not run there and failed with a raw traceback from
  inside a *sibling package*, naming a binary our own docs never mentioned.

  Now: declared in `_system_deps.py` (an ordinary apt package, unlike
  `fclones`) so the fleet-wide `scitex-dev ecosystem system-deps` aggregator
  sees it, and a missing binary raises `MissingSystemDependencyError` with
  install instructions — the same contract `scan` gives a missing `fd`.

  The check runs **before planning**, so the DEFAULT dry-run cannot promise a
  run whose transport could not start: "WOULD ARCHIVE 500 GB" on a box with
  no rsync is a confident claim about something never checked, discovered
  only at `--yes`. The library stays honest in the other direction — an
  injected `runner` *is* the transport, so `apply_archive`/`apply_restore`
  require the binary only when `runner is None`, and `plan_archive` never
  does.

  Worth recording why it was missed: nothing in this package spawns rsync.
  `_archive.py` calls `sync_dir()`, an ordinary function from an ordinary
  declared dependency, and the subprocess happens one package away. The PyPI
  dependency is the *adapter*; the binary it adapts needed declaring too. No
  import gate can catch that — `import scitex_ssh` resolves right up until
  the binary underneath is missing. Found by dogfooding, not by any check we
  own.

- New `scitex-storage validate-inodes [PATH ...]` — reports how close the mount
  backing each path is to **inode exhaustion**, which fails every write
  while `df` still shows free space (a Spartan project measured 96%
  inodes at 70% disk). Deliberately the cheapest thing in the package:
  one `statvfs` per path, so it is O(1) rather than O(files) — walking a
  multi-million-file tree to count inodes is self-defeating when
  metadata operations are already slow. It needs **no system binaries**
  (unlike `scan`, which needs `fd`) and **no login shell**, so it works
  from a bare job step, a cron line, or a container, i.e. when the
  richer tooling cannot run.

  Verdicts are three-state and never conflated: `measured`,
  `not-applicable` (btrfs/ZFS allocate inodes dynamically and report
  `f_files=0` — reported as such, *never* as a reassuring `0%`), and
  `could-not-look` (unreadable path, wedged mount). Exit codes carry the
  same distinction for unattended callers: `0` measured and under
  threshold, `1` at/over `--warn-at` (default 90%), `2` could not look.
  `2` is separate from `0` on purpose — a monitor that cannot tell
  "healthy" from "never read it" reports healthy for filesystems it
  never looked at.

  On a GPFS **independent fileset** (how HPC per-project directories are
  usually carved out) `statvfs` reports the *project's own quota*, so on
  those paths this answers the question that actually kills jobs — with
  no `mmlsquota` and no module load. Verified against Spartan's
  `check_project_usage` to within 3 inodes, and against `df -i`.

- `scan`'s directory walk now delegates to `fd` instead of Python
  `os.walk`, for multi-terabyte-scale performance. `fd` is a new
  **system** (non-PyPI) runtime dependency of `scan` only — see the
  README's "System dependencies" section — declared to the
  `scitex_dev.system_deps` federation. Missing the binary raises
  `MissingSystemDependencyError` with install instructions; there is no
  silent slow-path fallback. `scan`'s public API and read-only,
  stat-only contract are unchanged.
- New `scitex-storage find-duplicates PATH [PATH ...]` — a separate,
  explicitly opt-in verb that finds exact-duplicate files via `fclones`
  (a new system dependency of this verb only). Deliberately NOT part of
  `scan`: finding exact duplicates requires reading file contents, which
  would break `scan`'s "always safe on a nearly-full disk / network
  mount" guarantee.

## [0.2.0] - 2026-07-12

- `scitex-storage archive SOURCE --to nas|nas2 [--remote-path PATH] [--exclude PATTERN ...] [--checksum/--no-checksum] [--yes|-y] [--dry-run]`
  — move-not-delete tiering to nas/nas2 over ssh, built on scitex-ssh's
  `sync_dir` (rsync-over-ssh). Copy-verify-then-remove: pushes SOURCE,
  verifies the sync (checksummed by default), writes a manifest under
  `~/.scitex/scitex-storage/runtime/archive-manifests/`, and ONLY THEN
  removes the local copy. A failed sync leaves SOURCE completely untouched
  and no manifest is written. Defaults to a dry-run; `--yes`/`-y` is
  required to actually sync + remove (canonical mutating-verb flags per
  the ecosystem convention, unlike `images prune`/`sweep`'s `--apply` —
  `archive`/`restore` are audit-cli's hardcoded mutating-verb list, those
  two aren't). Creates the remote parent directory (`mkdir -p` via
  `exec_remote`, not `rsync --mkpath` — the latter needs rsync 3.2.3+,
  and a real destination still runs 3.0.7) before the sync, since a
  destination that's never held archived data has no
  `~/scitex-storage-archive/...` tree yet — the common first-use case,
  found by scitex-ssh smoke-testing a real archive against real nas2.
  Every shell command built from a remote path (the `mkdir -p` above,
  `rm -rf` for `restore --delete-remote`) leaves a leading `~` unquoted —
  a naive `shlex.quote()` of the whole path turns `~` into a literal
  character (tilde-expansion only applies unquoted), silently creating a
  directory named `~` instead of resolving `$HOME` — also found by
  scitex-ssh's real-nas2 smoke test. Both `archive` (push) and `restore`
  (pull) add a trailing `/` to the copy source so rsync copies its
  *contents* directly into the destination rather than nesting the source
  one level deeper as a subdirectory — without it, a restored tree landed
  two directories deep with byte-correct but wrongly-placed data, caught
  by scitex-ssh diffing actual restored file paths, not just checksums.
- `scitex-storage restore SOURCE [--delete-remote] [--yes|-y] [--dry-run]`
  — reads the manifest `archive` wrote for SOURCE and pulls the data back.
  The remote copy is kept by default; `--delete-remote` removes it after a
  verified restore. Defaults to a dry-run.
- `scitex-storage sweep DIRECTORY --threshold-files N [--min-age-hours H] [--apply --confirm NAME ...]`
  — tar an inode-hog directory in place (many small files -> one tar, one
  inode). Candidates are immediate children of DIRECTORY at or above
  `--threshold-files`, excluding anything whose newest file is younger than
  `--min-age-hours` (default 24h — protects a directory still in active
  use). `--apply` requires an explicit `--confirm NAME` per directory,
  never a blanket "sweep everything the plan found". Compute-node-only:
  refuses to run unless `$SLURM_JOB_ID` is set. Checks remaining SLURM
  walltime before starting each candidate and stops rather than risking a
  kill mid-tar. Defaults to a dry-run.
- `scitex-storage sweep-status DIRECTORY` — read-only listing of directories
  already swept (a sibling `<name>.tar` exists).
- `scan`'s `ChildUsage` gained a `newest_mtime` field (max file mtime in the
  subtree) — computed in the same walk as size/file-count, no extra
  traversal; used by `sweep`'s freshness exclusion and now also exposed in
  `scan --json` output.

- `scitex-storage images prune DIRECTORY [--keep N] [--pattern GLOB] [--apply]`
  — rotate a directory of versioned files (e.g. dated SIF builds), keeping
  the newest N plus every file any symlink in the directory currently
  references (those are never removed regardless of N). Before unlinking,
  also checks `/proc` for any process with the candidate open and skips it
  (loudly, never raises) — a second guard for a file that dropped out of
  the symlink target mid-swap but is still open by a running process.
  Defaults to a dry-run; `--apply` is required to actually delete.

## [0.1.0]

- Initial bootstrap: `scitex-storage scan [PATH ...]` MVP — a read-only,
  stat-only walk reporting the biggest space and inode (file-count)
  consumers per top-level child of a root (defaults to `~/.scitex` and
  `~/proj`). Never follows symlinked directories, never reads file
  contents, never mutates anything.
