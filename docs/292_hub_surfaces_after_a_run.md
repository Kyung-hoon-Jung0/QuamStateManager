# 292 — Chip Status after a new run: resolve each run folder once, share its dataset id

**Date:** 2026-10-06 · **Trigger:** S8 review P2-2 (docs/283). After every new run, each
ledger surface was recomputed from scratch, and on a 10,000-run chip Trends took 6-9 s.

## Measured cause (32 qubits, 2,000 runs, Trends right after one new run: 0.80-0.86 s)

cProfile of that request:
- 0.53 s in `_uid_for_run_ref`, which turns a point's run folder into its dataset id, once
  per point (2,002 calls). Each call ran `Path.resolve()`: two Windows file-system calls
  (`nt._getfinalpathname`).
- The folder → id memo lived on the per-request `LedgerTable`, so every request started
  from scratch.
- 0.36 s for the ledger read itself (S7 `value_history.read`); about 0.45 s of the
  probe's wall time was the honest "Preparing" while the RAM index caught up with the
  new run.

## Change

- `routes._resolved_folder` resolves each run-folder spelling once per process (bounded
  LRU, 65,536). A moved folder changes its spelling or the dataset roots, never this answer.
- `hub_status._shared_uid_memo` gives every request the same folder → uid map, keyed by
  the dataset roots' spelling (the token's own roots component). It holds at most 8 root
  sets, and a set starts over past 200,000 folders.
- No answer changes: the uid is a pure function of (folder, run id, roots).

## After (same probes)

| Chip | Surface | Before (review) | Before (this PC) | After |
|---|---|---:|---:|---:|
| 2,000 runs | Trends after a run | ~1.0 s | 0.80-0.86 s | 0.28-0.30 s |
| 2,000 runs | grid after a run | 0.85-1.0 s | — | 0.25-0.31 s |
| 10,000 runs | Trends after a run | 6.0-8.9 s | — | 0.94-1.07 s |
| 10,000 runs | grid after a run | 5.6-8.6 s | — | 0.68-0.85 s |
| 10,000 runs | meta after a run | 1.0-1.5 s | — | 0.46-0.68 s |

Warm requests (nothing changed) stay at 1-45 ms.

## Pins

`tests/test_hub_status_after_a_run.py`:
- after a new run, Trends and the grid resolve at most that run's folder;
- the requests share one folder → uid memo;
- a second drawer request resolves no run folder;
- one memo per root set.

Mutation sweep: 3/3 red (resolve per call, a per-request memo, one memo for every root
set). Related tests: 665 passed (`test_hub_*`, metric meta, Param History, Trends,
Calibration log, Column/field history).

## Still open

- The ledger read is still redone over every point after each run: about 0.2 s at 2,000
  runs and about 0.6 s at 10,000. Incremental derivation (append the new events' points
  to the previous answer) is the next step. It needs care: an undo or a "source gone"
  flag rewrites an OLD event's points in place, so the ledger is not purely append-only.
- The token still carries the chip's `mutation_seq`, so a staged, unapplied edit of an
  unrelated field also recomputes everything (now about as costly as a new run).
  Narrowing it means keying on what a read actually depends on: each path's current
  holder (aliases) and, for the meta's `matches_current`, its current value.
