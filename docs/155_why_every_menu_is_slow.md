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

### 5a. Measured (same fixture, 390 date dirs)

| route | before | F1+F2 | +F2' | total |
|---|---|---|---|---|
| `/topology` (Chip Status / Overview) | 7,831 | 403 | **403** | **-95%** (~14.1 s -> ~0.7 s on the share) |
| `/param-history/alignment` | 1,180 | 1,180 | **401** | **-66%** |
| `/datasets` | 1,564 | 1,174 | **784** | **-50%** |
| `/trends/data` | 1,564 | 1,174 | **784** | **-50%** |
| `/datasets/poll` (every page, polled) | 782 | 392 | **392** | **-50%** |
| `/field/history` (the 🕘 popover) | 789 | 399 | **399** | **-49%** |

`/topology`'s attribution afterwards shows exactly ONE `_current_mtime` sweep,
where it was ten; the alignment path's per-entry `resolve()` storm is gone
entirely.

Pinned by `tests/test_share_io_cost.py` (16 asserts) plus the docs/154 pins in
`tests/test_workspace_walk_depth.py`, all counting syscalls and never a clock.
**12/12 mutations caught**, including the three docs/154 mutations re-verified
against the new code shape.

**A pin can go blind without failing.** docs/154's spy hooked `Path.stat` and
`Path.iterdir`; F2' moved the walk onto `os.scandir` + `os.stat` and every one
of those pins started asserting against an empty set — they had stopped
watching the walk and would have passed a mutation that deleted it. The spy is
hooked at the `os` layer now, which both spellings reach and neither can
bypass. A spy that sees only one spelling of a syscall pins the spelling, not
the cost.

Regression: 417 passed across the dataset/history/RB/poll/project suites. The
two failures are the pre-existing set (§7): the docs/87 tmp-path case class,
and one poll-stability concurrency flake measured at 20/20 passing WITH this
change and 17/20 without it.

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

## 6. The backlog after F1 + F2 + F2' — every item measured, none guessed

Attribution of what is LEFT, on the shipped code at 390 date dirs. Frame counts
are exact, not sampled.

```
/datasets  ·  /trends/data        784 ops     <- was 1,564
   390  history.py  _workspace_token   (one os.stat per date dir)
   390  dataset.py  _current_mtime     (one os.stat per date dir)

/param-history/alignment          401 ops     <- was 1,180
   390  history.py  _workspace_token   (one os.stat per date dir)

/topology                         403 ops     <- was 7,831
/datasets/poll  ·  /datasets/wait 392 ops     <- was 782
```

What is left is one `os.stat` per date dir per walker, and there are two
walkers doing the same walk (F3'). Nothing above is a repeated call any more.

**F3 — a request-scoped fingerprint memo. DEFERRED, on the measurement.** The
case for it was "the same call repeats inside one request", and F1 removed the
repetition. `/datasets` now spends its 784 on `_workspace_token` (390) and
`DatasetStore._current_mtime` (390) — two DIFFERENT functions called once each,
which no memo merges. There is no longer enough on the table to justify
touching what docs/105 #8 decided deliberately. Revisit only with a
measurement that shows repetition returning.

**F3' — the two walkers walk the same tree.** What the attribution actually
shows is a duplicate SWEEP, not a repeated call: `_workspace_token` stats every
date dir to answer "did the workspace change", and `_current_mtime` stats every
date dir to answer "did this store's root change". Same directories, same
syscalls, two answers. Merging them — one sweep feeding both — is the honest
version of what F3 was reaching for, and it is worth roughly another 50% on
`/datasets` and `/trends/data`. It needs the two staleness contracts to be
reconciled first, which is real design work.

**F4 — the two every-page polls read a watcher instead of sweeping.**
`/datasets/poll` and `/datasets/wait` each re-derive a fingerprint that
`core/run_watch.py`'s thread (docs/141 §4p) already computes on a 0.5 s timer.
Reading the watcher's last tick would take both to ~0 ops. This is a design
change, not a patch: ownership of "is the archive stale" moves from the request
to the watcher, and every consumer's staleness contract has to be re-argued
against that.

**F5 — the O(dates) sweep is unbounded by construction.** Even at one syscall
per date dir, a five-year archive is ~1,800 round-trips per poll. Nothing in
the app ever caps, tiers or ages this. Any real answer is F4 or an OS change
notification (`ReadDirectoryChangesW`), not a smaller constant.

**F6 — `/load` costs 804 share ops** (~1.4 s on the share) and was not
investigated. It is a user-initiated action rather than a background one, so it
ranks below the others, but it is the same order of magnitude as a poll.

**F7 — tests write into the developer's real `instance/` dir.** Observed during
this work: `instance/workspace_cache/` gained entries while the suite ran, and
`instance/last_session.json` had been overwritten with a `pytest-of-Measurement`
tmp path. The directory is gitignored so nothing reaches a commit, and
`_purge_test_leftovers` already mops up leaked *history* dirs — but the cache
and the session file are not covered, and the failure mode is a developer's own
SM state being silently replaced by a test's. Unrelated to performance; found
here, recorded here.

## 7. What this does not show

- **The NAS was unreachable throughout** (the same network fault that ended the
  docs/154 session). Every "est. on NAS" figure is op-count x 1.8 ms, and the
  1.8 ms itself is derived from one earlier profile. The op counts are measured;
  the seconds are arithmetic.
- **The customer's real date-dir count is unknown.** 390 is a modelled
  13-months-of-daily-runs, chosen to make the scaling visible, not read off their
  archive. Their absolute numbers could be smaller or considerably larger.
- Only the server side was measured. Client-side render cost (docs/141's subject)
  is a separate axis and is what dominates `/bulk` locally.
- Surfaces reachable only by clicking through a run (figures, h5 reads) were not
  swept.
- The measuring harness itself (`scratchpad/serve_instrumented.py`) wraps `open`
  and `os.stat` process-wide; it changes no SM code, but its own overhead is
  inside every millisecond figure above. Op counts are unaffected.
