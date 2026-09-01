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

## 5. Fixes, in value-per-risk order — proposed, not yet implemented

**F1 — `/topology`: one staleness sweep per render, not one per edge.** Resolve
the dataset stores once for the whole RB derivation and hand `derive_for_edges` a
resolver that reuses them. 7,831 -> ~800 ops (-90%, ~14 s -> ~1.4 s on the
share). Contained to the docs/138 enrichment path; touches no contract.

**F2 — halve `_current_mtime`.** `entry.is_dir()` on a `Path` from `iterdir()` is
a syscall; the same answer comes free from `os.scandir`'s `DirEntry`, which
carries the file-type bits from the directory listing. Keep `os.stat` for the
**mtime** — docs/141 §4ac established by measurement that `DirEntry.stat()` on
Windows serves a cached mtime that never sees a write *inside* the directory,
which is precisely the change this function exists to detect. 2 ops per date dir
-> 1, for every caller, at no semantic cost.

**F3 — a request-scoped fingerprint memo.** docs/105 #8 rejected a *TTL* memo,
correctly: a run written milliseconds before a poll must be seen by that poll. A
memo scoped to one HTTP request keeps that contract exactly — each poll is its
own request and re-samples — while collapsing every repeated call inside one
render. Needs care: the scheduler and autofit workers are not request-scoped, so
the memo must be keyed on a real request context and simply not exist outside
one.

**F4 — the two every-page polls.** With F2 they halve. Beyond that, both
`/datasets/poll` and `/datasets/wait` re-derive the same fingerprint that
`core/run_watch.py`'s watcher thread (docs/141 §4p) is already computing on a
timer; making the polls read the watcher's last tick instead of sweeping
themselves would take them to ~0, but that is a design change, not a patch.

## 6. What this does not show

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
