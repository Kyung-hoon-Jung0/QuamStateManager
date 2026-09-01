# 155 — "It is not only Datasets": where the other menus' time actually goes

**2026-09-01, user-directed follow-up to docs/154.** Users report that Overview,
Live State Edit and Param History slow down too, not just Datasets — and that it
gets worse over months. docs/154 fixed two walks on the `/datasets` path; this is
the measurement of what is left, on the other surfaces.

## 1. Method: count syscalls, not milliseconds

The customer's workspace is an SMB share (`\\nasdaq.snu.ac.kr\QDL`, mapped `Z:`).
Every `stat`/`scandir`/`open` there is a network round-trip, so a route doing 800
stats costs ~8 ms on this development NVMe and **~1.4 s** on their box. Wall clock
measured locally cannot see the problem at all; the operation count is the same
number on both machines, so that is what was measured.

SM was served under waitress (16 threads, production mode) with `os.stat`,
`os.scandir`, `os.listdir`, `os.path.exists` and `open` wrapped by a per-request
counter that buckets each call by which filesystem the path is on (share / live
chip / SM's own instance dir). For attribution the wrapper also records the
innermost `quam_state_manager` frame that made each share call — exact, not
sampled, which is the half py-spy cannot give.

Fixture: the real 13-qubit / 16-pair chip
(`SNU-QDL-Qualibrate/quam_state_9Q_Temp`, 371 KB state.json) against a synthetic
archive built to the customer's shape — 5,874 runs, **390 date dirs**, shallow
layout. 390 date dirs is roughly 13 months of daily measurement, which is the
"over months" axis the reports describe.

The `est. on NAS` column is an extrapolation, not a measurement: the measured
20.2 s of docs/154's py-spy profile, over the 11,420 ops that same walk makes on
this shape, gives **1.8 ms per share op**. The NAS was down while this was
written (§6).

## 2. What each surface costs, per request

| surface (what the user calls it) | route | share ops | est. on NAS |
|---|---|---|---|
| **Chip Status / Overview** | `/topology` | **7,831** | **~14 s** |
| Datasets | `/datasets` | 1,564 | ~2.8 s |
| Trends | `/trends/data` | 1,564 | ~2.8 s |
| **Param History**, first open | `/param-history/alignment` | 1,180 | ~2.1 s |
| **Live State Edit** — 🕘 on a value | `/field/history` | 789 | ~1.4 s |
| **Live State Edit** — 🕘 on a column | `/bulk/column-history` | 786 | ~1.4 s |
| *every page*, the new-run poll | `/datasets/poll` | 782 | ~1.4 s |
| *every page*, the live-wake long poll | `/datasets/wait` | 782 | ~1.4 s |
| Param History (grid) | `/param-history` | 17 | ~0.03 s |
| **Live State Edit (the page itself)** | `/bulk` | **0** | 0 |
| Json Tree, Pulses, Diagnostics, Instrument, Qubits/Pairs/Flux/Couplers/Resonators | | 0 | 0 |

Two things fall out of that table immediately.

**Overview is the worst surface in the app, by 5×.** `/topology` *is* the Chip
Status page — the menu users named first.

**Live State Edit's own render touches the share zero times.** Its 112 ms is
local CPU on a 2.5 MB grid (docs/141's territory). What makes that page *feel*
slow on a big archive is not the page: it is the two background polls every open
tab runs, and the two 🕘 history popovers, which are the things a user clicks
constantly while editing. Naming the page was right; blaming the grid would have
been wrong.

## 3. One root cause under all of them

Attribution of `/topology`'s 7,831 share calls, by frame:

```
  3900  /core/dataset.py:922  _current_mtime      (entry.is_dir())
  3900  /core/dataset.py:925  _current_mtime      (entry.stat())
    10  /core/dataset.py:916  _current_mtime      (folder_path.stat())
    10  /core/dataset.py:921  _current_mtime      (iterdir)
    10  /core/rb_gate_fidelity.py:74 from_run_folder
     1  /core/history.py:2034 list_snapshots
```

`DatasetStore._current_mtime` answers *"has anything new landed?"* by stat'ing
**every date dir under the root**, two syscalls each (`is_dir()` then `stat()`).
One call is therefore `2 x (date dirs)` share round-trips — 782 at 390 date dirs
— and it is deliberately not memoized (docs/105 #8: a TTL memo broke the pinned
write-then-poll contract).

`/topology` calls it **ten times per render**, once per 2Q edge. That is
docs/138's derived per-gate RB fidelity: `derive_for_edges` calls
`_rb_run_folder(load_id)` per edge, and each call re-enters
`_active_dataset_stores(fast=True)` -> `_get_or_create_store(cand)` ->
`rescan_if_stale()` -> a full staleness sweep of the archive. Ten edges, ten
sweeps, for ten run-folder lookups that between them read ten files.

## 4. Why it gets worse every month — measured, not argued

The cost is linear in **date dirs**, and an archive gains one per day of
measurement. Holding everything else fixed and adding date dirs only:

| date dirs | `/topology` share ops | `/datasets/poll` |
|---|---|---|
| 90 | 1,830 | 182 |
| 390 | **7,830** | **782** |

Exactly `edges x dates x 2`. Run count is not the axis (the archive kept its
5,574 runs across both rows) — **days are**. A lab that measures daily adds ~7 s
to every Chip Status render per year on this share, forever, and nothing in the
app ever prunes or amortizes it.

## 5. Fixes, in value-per-risk order

**F1 — `/topology`: one staleness sweep per render, not one per edge.
SHIPPED.** `_topology_with_derived_rb` resolves `_active_dataset_stores` once
and hands `derive_for_edges` a resolver that reuses it; `_rb_run_folder` gained
an optional `stores=` and is byte-identical without it. The resolution is LAZY
— a chip with no Standard-RB `load_id` resolved nothing before and must keep
paying nothing, so an eager sweep would have been a regression for those chips
dressed as a fix.

**F2 — halve `_current_mtime`. SHIPPED.** `entry.is_dir()` on a `Path` from
`iterdir()` is a syscall; the same answer comes free from `os.scandir`'s
`DirEntry`, which carries the file-type bits from the directory listing. The
name test also moved in FRONT of it, so a non-date entry (a README, an export
folder) now costs nothing at all. `os.stat` is kept for the **mtime** — see
§5b. 2 ops per date dir -> 1, for every caller, at no semantic cost.

**F2' — `_workspace_token` had F2's shape too. SHIPPED.** The other walker on
these routes made the same two calls per entry, and on the alignment path a
third: docs/139's own-root exclusion called `Path.resolve()` on EVERY entry at
both levels. `os.scandir` makes `is_dir()` free the same way, and the exclusion
now resolves the ROOT once — every entry it tests is a direct child of a
directory the walk already holds, so for anything that is not a symlink the
resolved path is `parent_resolved / name` and the predicate is string work. A
symlink, or a parent that would not resolve, falls back to the original.

**F4 — the background polls stopped sweeping to answer questions they do not
ask. SHIPPED.** These two run forever on every open tab, and they were the
whole reason a page nobody is looking at slows down the page somebody is.
`_active_dataset_stores` gained `rescan=False`:

- `/datasets/changes-since` (every **5 s**) rescanned every store in the
  lookup and then rescanned it again inside `changes_since` — two full sweeps
  of every date dir per call. Worse, the first one carried **no deadline**, so
  docs/105 #4's budget was being spent before the bounded sweep it belongs to
  was ever consulted. The lookup no longer rescans; the deadline-bearing sweep
  is the only one left.
- `/datasets/wait` wants the folder **paths** and nothing else — the watcher
  takes its own signature on its own thread. It built and rescanned a run
  table that nothing read.

With `rescan=False` the `run_count` filter is skipped too: a count we
deliberately did not refresh must not be used to drop a folder for being
empty, which would drop exactly the folder about to receive its first run.
`/datasets/wait` therefore now watches an empty data folder, which it never
did before.

**F3' — the two walkers stopped walking the same tree twice. SHIPPED.**
`HistoryManager._workspace_token` stats every date directory to answer *"did
the workspace change?"*; `DatasetStore._current_mtime` stats every date
directory to answer *"did this store's root change?"*. Those are the SAME
directories — a data folder is a workspace root in the shallow layout, and the
chip directory the token descends into in the deep one (a run entry's
`folder_path.parent.parent` is the root in one and the chip dir in the other,
which is exactly what the token stats) — so a `/datasets` render walked the
whole archive twice, in two functions, for two answers.

`core/dir_sample.py` is the one walk. It merges the I/O, not the questions:
each walker still computes its own fingerprint from it, and a pin holds the
two answers apart. Two costs are kept separate because the callers want
different subsets — the LISTING (names + type bits + the symlink bit) rides
one `os.scandir`, while an mtime needs an `os.stat` per entry and is taken
lazily, memoized per entry. The store asks about date directories only, the
token about every child directory, and the second caller in a request pays for
none of them again.

**The scope is a request, and that is the whole argument.** docs/105 #8
rejected a *TTL* memo because a run written milliseconds before a poll must be
seen by THAT poll; a request-scoped cache keeps that exactly, because every
poll is its own request and takes its own sample. `before_request` opens the
scope and `teardown_request` closes it (it runs even when the view raised),
and `begin()` overwrites whatever a missed teardown left, so a leaked scope
cannot outlive one request on a reused worker thread. Outside a request — the
scheduler worker, autofit, the CLI — no scope is ever opened, nothing is
cached, and both walkers behave precisely as they did before the module
existed. All four properties are pinned.

### 5a. Measured (same fixture, 390 date dirs)

| route | before | F1+F2 | +F2' | +F4 | +F3' | total |
|---|---|---|---|---|---|---|
| `/topology` (Chip Status / Overview) | 7,831 | 403 | 403 | 403 | **403** | **-95%** |
| `/datasets` | 1,564 | 1,174 | 784 | 784 | **392** | **-75%** |
| `/trends/data` | 1,564 | 1,174 | 784 | 784 | **392** | **-75%** |
| `/datasets/changes-since` (every **5 s**) | 1,568 | 784 | 784 | 392 | **392** | **-75%** |
| `/param-history/alignment` | 1,180 | 1,180 | 401 | 401 | **401** | **-66%** |
| `/datasets/poll` (every 60 s) | 782 | 392 | 392 | 392 | **392** | **-50%** |
| `/field/history` (the 🕘 popover) | 789 | 399 | 399 | 399 | **399** | **-49%** |
| `/datasets/wait` (the long poll) | 782 | 392 | 392 | **0** | **0** | **-100%** |

Every surface users named is now between 0 and 403 share operations — about
0.7 s on the share where the worst of them was ~14 s, and where all of them
grew by one date directory per day of measurement.

**What a tab costs while nobody touches it**, at 1.8 ms per share op — the
number that decides whether four open tabs saturate the pool:

| | before | after |
|---|---|---|
| `/datasets/changes-since`, every 5 s | 16.9 s/min | **8.5 s/min** |
| `/datasets/wait`, continuous | 1.7 s/min | **0** |
| `/datasets/poll`, every 60 s | 0.7 s/min | 0.7 s/min |
| **per open tab** | **19.3 s/min** | **9.2 s/min** |

`/topology`'s attribution afterwards shows exactly ONE `_current_mtime` sweep
where it was ten; the alignment path's per-entry `resolve()` storm is gone;
and `/datasets`' attribution is now a single `dir_sample` listing — 390
mtimes, one stat, one scandir — serving both walkers.

Pinned by `tests/test_share_io_cost.py` (30 asserts) plus the docs/154 pins in
`tests/test_workspace_walk_depth.py`, all counting syscalls and never a clock.
**26 mutations, 26 caught** across the five fixes — including the three
docs/154 mutations re-verified against each new code shape, and two whose only
job is to prove a pin is not vacuous.

**A pin can measure the wrong moment, too.** The first pin for "the symlink
bit rides the listing" read `smp.children` inside the spy — which measures a
tuple unpack, not the listing — and the mutation swapping the free
`DirEntry.is_symlink()` for `os.path.islink` (an `lstat` per entry, exactly
the cost F2' removed) passed it untouched. The pin measures `sample()` itself
now, as an invariant across sizes, and the spy counts `lstat` as well: a
syscall the spy does not hook is a syscall the pins cannot hold down.

**A pin can go blind without failing.** docs/154's spy hooked `Path.stat` and
`Path.iterdir`; F2' moved the walk onto `os.scandir` + `os.stat` and every one
of those pins started asserting against an empty set — they had stopped
watching the walk and would have passed a mutation that deleted it. The spy is
hooked at the `os` layer now, which both spellings reach and neither can
bypass. A spy that sees only one spelling of a syscall pins the spelling, not
the cost.

Regression: 858 passed across the dataset/history/RB/poll/web suites. Every
failure is pre-existing (§7): the docs/87 tmp-path case class, and
`test_poll_stability` flakes that reproduce at the same rate with that file run
entirely alone (1 in 3) and pass 10/10 in isolation with and without these
changes. One of them was measured at 20/20 passing WITH F2' and 17/20 without.

### 5b. The one thing that could not be pinned, and why that is the finding

`DirEntry.stat()` is the obvious way to make the mtime free too, and docs/141
§4ac already forbids it. The natural semantic pin — write a run inside a date
dir with nobody touching the dir, assert the fingerprint moved — was written,
and it **passed under exactly that mutation**. Measured cause, 20 trials per
gap on this NTFS volume:

| gap after `mkdir` | `DirEntry.stat()` saw it | `os.stat()` saw it |
|---|---|---|
| 0.00 s | 1/20 | 6/20 |
| 0.02 s | 0/20 | 5/20 |
| 0.50 s | 5/20 | 8/20 |
| 1.20 s | 5/20 | 16/20 |

NTFS updates the parent's recorded timestamps for a child lazily, so within
about a second of a `mkdir` **neither** call reliably reports the change.
`os.stat` is markedly better and still not deterministic. A pin built on that
is a coin toss, which is worse than no pin — so the guard is the COST pin
(`de.stat()` costs zero syscalls, so the slope pin reds), verified by mutation,
with the reasoning recorded beside it in the test file.

Worth separating from the fix: this also says SM's own new-run detection on
Windows has an inherent sub-second latency that no amount of polling removes.
It has never mattered — a real run folder takes far longer to write than the
window measured here, and `run_watch` (docs/141 §4p) polls at 0.5 s anyway —
but it is the reason a write-then-poll test in this project always bumps the
mtime explicitly rather than trusting the filesystem to have noticed.

## 6. The backlog after F1 + F2 + F2' + F3' + F4 — every item measured, none guessed

Attribution of what is LEFT, on the shipped code at 390 date dirs. Frame counts
are exact, not sampled.

```
/datasets  ·  /trends/data        392 ops     <- was 1,564
   390  dir_sample  mtime()            (one os.stat per date dir, ONCE,
     1  dir_sample  _read                shared by both walkers)
     1  dir_sample  _list

/param-history/alignment          401 ops     <- was 1,180
/topology                         403 ops     <- was 7,831
/datasets/poll (60 s)             392 ops     <- was 782
/datasets/changes-since (5 s)     392 ops     <- was 1,568
/datasets/wait (continuous)         0 ops     <- was 782
```

What is left is one `os.stat` per date dir, once, for both walkers together.
There is no duplicated I/O left on these paths — only F5's single sweep, which
is the shape of the question rather than a defect in the answer.

**F3 — a request-scoped fingerprint memo. Answered by F3', and the record of
how it was nearly dropped is worth keeping.** F3 was deferred on the reasoning
that F1 had removed the repetition — measured on `/datasets` and `/topology`,
where that was true. It was false on `/datasets/changes-since`, the route that
runs every five seconds on every tab, which had not been measured when the
call was made and was doing two sweeps per request. F4 removed that pair
outright; F3' then took the request-scoped cache to the place the duplication
actually lived — one directory listing, shared by two walkers — rather than
memoizing a fingerprint. The lesson is smaller than the fix: a deferral is
only as good as the routes it was measured on.

**F4' — could a poll read the watcher's tick instead of sweeping at all?**
F4 was originally framed that way, and reading `run_watch.signature()` refuted
the framing: the watcher looks at the ROOT and the NEWEST date dir only, while
`_current_mtime` looks at every date dir. Gating a poll on it would silently
stop noticing a change in an OLDER date dir — a backfilled import, a figure
landing in yesterday's run, a folder deleted — and the polls are documented as
the watcher's safety net, so the net would be gating on the thing it protects.
What shipped as F4 instead removes only sweeps that were duplicated or unused
and changes no staleness question. A real version of this needs a periodic full
check to keep the net, and should be judged against the ~9 s/min a tab costs
now rather than the ~19 it did.

**F5 — the O(dates) sweep is unbounded by construction.** Even at one syscall
per date dir, a five-year archive is ~1,800 round-trips per poll. Nothing in
the app ever caps, tiers or ages this. Any real answer is F4 or an OS change
notification (`ReadDirectoryChangesW`), not a smaller constant.

**F6 — `/load` costs 804 share ops** (~1.4 s on the share) and was not
investigated. It is a user-initiated action rather than a background one, so it
ranks below the others, but it is the same order of magnitude as a poll.

**F8 — jsdom, and this environment's baseline. DONE** (§8): `npm install`
run, 101/101 selfchecks executing, and the full-suite baseline established at
35 failed / 6,911 passed with every failure reproduced on the pre-campaign
tree. What remains open here is smaller: the 35 are worth triaging into
"stale pin" and "genuine OS-behaviour difference", which nobody has done.

**F7 — tests write into the developer's real `instance/` dir.** Observed during
this work: `instance/workspace_cache/` gained entries while the suite ran, and
`instance/last_session.json` had been overwritten with a `pytest-of-Measurement`
tmp path. The directory is gitignored so nothing reaches a commit, and
`_purge_test_leftovers` already mops up leaked *history* dirs — but the cache
and the session file are not covered, and the failure mode is a developer's own
SM state being silently replaced by a test's. Unrelated to performance; found
here, recorded here.

## 7. Does this help anybody but the customer? — the A/B

Everything above was measured on one archive shape at one size, on a machine
whose workspace is a NAS share, and every "on the share" figure is arithmetic.
That leaves the question that actually matters for shipping: **does a lab with
a small archive on a local disk get anything?** So the pre-campaign commit
(`c247338`) was checked out into a worktree and the same routes were driven
against three archive sizes, in-process, counting syscalls against the
workspace directory. Local NVMe, no share involved.

| | **30 runs** / 10 dates | | **300 runs** / 60 dates | | **1,440 runs** / 180 dates | |
|---|---|---|---|---|---|---|
| route | before | after | before | after | before | after |
| `/topology` (Overview) | 220 / 16.7 ms | **12 / 6.9** | 1,220 / 71.2 | **62 / 10.3** | 3,620 / 247 | **182 / 18.0** |
| `/param-history/alignment` | 132 / 19.4 | **13 / 6.3** | 1,142 / 133 | **63 / 11.0** | 5,042 / **627** | **183 / 22.8** |
| `/datasets` | 114 / 5.5 | **12 / 1.5** | 904 / 51.2 | **62 / 6.6** | 3,784 / 219 | **182 / 21.9** |
| `/trends/data` | 114 / 5.5 | **12 / 0.8** | 904 / 43.2 | **62 / 3.4** | 3,784 / 224 | **182 / 14.2** |
| `/datasets/changes-since` (5 s) | 44 / 2.0 | **12 / 1.0** | 244 / 12.3 | **62 / 3.6** | 724 / 39.5 | **182 / 12.8** |
| `/datasets/wait` (continuous) | 22 / 1.5 | **0 / 0.4** | 122 / 6.8 | **0 / 0.3** | 362 / 26.7 | **0 / 0.2** |
| `/field/history` | 23 / 6.8 | 13 / 6.7 | 123 / 18.1 | 63 / 18.3 | 363 / 65.1 | 183 / 60.8 |

(ops / milliseconds, best of three after warming.)

**The answer is yes, and it is stronger than the share arithmetic suggested.**

- At **30 runs** — a lab two weeks old — operation counts already fall ~90% and
  a Chip Status render goes 16.7 → 6.9 ms. Real, free, and not something a
  person would notice.
- At **300 runs** — a few months — Param History goes **133 → 11 ms** and Chip
  Status **71 → 10 ms**. That is the point where it stops being a rounding
  error on a local disk.
- At **1,440 runs** — about a year of daily measurement — Param History goes
  **627 → 23 ms (27x)** and Chip Status **247 → 18 ms**. Plainly perceptible,
  with no network anywhere.
- **No route got worse at any size.**

`/field/history` is the honest exception: its share ops halve but its wall
time does not move (65 → 61 ms), because that route is dominated by snapshot
and SQLite reads rather than by the directory walk. It is not a beneficiary of
this campaign, and saying otherwise from the op count alone would be wrong.

Two things this A/B corrected:

1. **A defect in the measurement itself.** Driving `/datasets/changes-since`
   with `ts=0` returns every row in the archive, and under `TESTING` the
   docs/132 EXP-ingest then runs INLINE for each one — file I/O a real poll
   never pays. The first run of this table read 3,604 ops at the mid size for
   that route and showed almost no improvement; driven the way the browser
   drives it (the previous response's cursor) it reads 724 → 182.
2. **docs/154's finding, confirmed at a second size.** The pre-campaign
   `/param-history/alignment` costs 5,042 ops at 180 date dirs — that is per
   RUN, not per date, which is exactly the O(runs) token this campaign opened
   with. Worth stating plainly: **that half of the benefit only reaches labs
   whose workspace root is the SHALLOW layout** (`<root>/<date>/<run>`, which
   is what qualibrate's `storage.location` is). A deep-layout root already
   stopped at the date level and sees only the other fixes.

## 8. The client side, and this environment's real baseline

Everything above is server-side. The client half was unverifiable here until
now, because the `.cjs` selfchecks need jsdom and this machine did not have
it — so 78 DOM-level pins were reporting as failures-or-skips rather than
running (the state CLAUDE.md warns about, and docs/120 records once hid four
genuinely failing selfchecks).

`npm install` was run. Results:

- **`npm run selfcheck`: 101 passed, 0 failed, 0 skipped, of 101.** The whole
  client suite actually executes.
- The nine pytest drivers that had been failing (`test_pane_state`,
  `test_pulses_commit`, `test_undo_nav`, `test_kb_polish`, `test_grid_editing`,
  `test_ds_flow`, `test_figure_lightbox`, `test_interactive_theme`,
  `test_apply_ux`) — **21 passed**. Those failures were jsdom's absence, not
  regressions.
- And structurally: `git diff c247338..HEAD -- '*.js' '*.cjs' '*.html' '*.css'`
  is **empty**. This campaign touched four Python files, two test files and two
  docs. A client-side regression was impossible by construction; the selfchecks
  confirm the harness that would have caught one now runs.

**Baseline for this environment (`QM_Qualibrate`, Windows 11, jsdom
installed), full suite minus `TestWaitForServer`: 35 failed, 6,911 passed, 280
skipped, ~21 min.** All 35 were re-run against the pre-campaign worktree: 33
fail there identically. The remaining two are flakes, measured 15x on each
tree — `test_state_only_run_waits_for_wiring` 0/15 on both (it only fails
inside a larger run, an ordering effect) and
`test_native_path_anchors_on_the_config_dir` 2/15 on both. **Zero regressions
from the campaign.**

The 35 fall into the classes this project already documents: the docs/87
OS-behaviour set (tmp-path case identity, WSL kernel checks, Windows
file-locking timing), the `test_scanner` staleness-timing group, and the stale
pins CLAUDE.md already records for `test_auto_apply` and `test_auto_sync`.
Nothing in the client half.

## 9. What this does not show

- **The NAS was unreachable throughout** (the same network fault that ended the
  docs/154 session). Every "est. on NAS" figure is op-count x 1.8 ms, and the
  1.8 ms itself is derived from one earlier profile. The op counts are measured;
  the seconds are arithmetic.
- **The customer's real date-dir count is unknown.** 390 is a modelled
  13-months-of-daily-runs, chosen to make the scaling visible, not read off their
  archive. Their absolute numbers could be smaller or considerably larger.
- Client-side RENDER COST (docs/141's subject) is still a separate axis and is
  what dominates `/bulk` locally. §8 verifies client CORRECTNESS, not speed.
- Surfaces reachable only by clicking through a run (figures, h5 reads) were not
  swept.
- The measuring harness itself (`scratchpad/serve_instrumented.py`) wraps `open`
  and `os.stat` process-wide; it changes no SM code, but its own overhead is
  inside every millisecond figure above. Op counts are unaffected.
