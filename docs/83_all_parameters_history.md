# 83 — every numeric parameter's history

*Status: shipped 2026-08-07. Branch `feat/param-history-all-leaves`.
Amends docs/20 (Param History) and docs/23 (its performance work).*

## The ask

> Param History에서 사용자가 "State에 있는 모든 숫자 파라미터"에 대해서는
> 알수없는지 물어보네

Param History tracked eleven curated properties — T1, T2ramsey, f_01, the
fidelities, three amplitudes. Everything else on the chip was invisible to the
dashboard, and reachable only through the 🕘 popover's fallback tier.

The question was whether "all of them" is affordable. It is — measured, it costs
**less** than what the eleven cost today.

## What it actually cost before

The popover's fallback re-parses snapshot `state.json` files newest-first and
stops after 150. On a real 264-snapshot chip:

```
qubits.qA1.T1                        2.6 ms   index   264 snapshots, complete
qubits.qA1.resonator.depletion_time  555 ms   scan    150 of 264, truncated
qubits.qA1.resonator.time_of_flight  491 ms   scan    150 of 264, truncated
```

So "all parameters" was not a missing feature so much as a **slow, truncated,
undiscoverable** one.

## Why the obvious design fails

Index every numeric leaf of every snapshot:

| chip | numeric leaves | snapshots | rows | at the ~235 B/row the existing index really costs |
|---|---|---|---|---|
| LabA | 7,992 | 111 | 887k | ~200 MB |
| 17Q | 8,424 | 264 | 2.2M | ~500 MB |

Not acceptable for one chip's sidecar.

## Why change points work

Measured over the real snapshot series:

```
                 numeric leaves    leaves that CHANGE between neighbours
LabA                     7,992    median 4    p90 23    max 832
17Q                       8,424    median 3    p90 17    max 2,716
ExampleChip_1Q                 1,300    median 2    p90 18    max 818
LabB                         550    median 2    p90 10    max 87
```

A calibration run rewrites almost nothing. Storing only the transitions turns
887k rows into ~12k — and the whole-chip index ends up **smaller than the
eleven-property one it sits beside**:

| chip | snapshots | parameters | change points | file | curated index |
|---|---|---|---|---|---|
| LabA | 111 | 10,981 | 12,072 | **2.02 MB** | 6.1 MB |
| 17Q | 264 | 11,344 | 17,256 | **2.22 MB** | 13.4 MB |
| ExampleChip_1Q | 1,154 | 2,902 | 15,312 | **1.09 MB** | 26.9 MB |
| LabB | 659 | 1,186 | 4,803 | **0.47 MB** | 8.5 MB |

One path's full history: **0.01–0.06 ms**. Full rebuild: **1.2–5.4 s**.

The ingest is nearly free because the snapshot's `state.json` is **already
parsed** for the curated rows — the added walk is +7–39 % on top of a parse we
were doing anyway.

## The design (`core/leaf_index.py`)

Three tables inside the chip's existing `index.sqlite`: `leaf_snaps`,
`leaf_paths`, `leaf_cp` — the repeated strings stored once, so a change point
is three integers. The file is already per-chip, WAL and routed through the
chip-identity ladder; a second file would have to re-derive all of that.

**Its own version marker** (`leaf_meta`). `PRAGMA user_version` belongs to
`param_history` and drives its pair-row upgrade — an upgrade here must never
trigger one there.

**Pointers are followed.** QUAM avoids duplicating values by storing pointers,
so 1,000–2,300 leaves per snapshot are pointer strings and 570–1,190 of them
resolve to numbers — on a 1Q chip as many parameters again as the direct ones,
and they are exactly the fields users click
(`xy.operations.x180.amplitude`). Resolving them costs 3–7 ms per snapshot and
returns the same number the scan tier already returns. A pointer that resolves
to nothing numeric is recorded as such and handed back to the scan, which can
at least show the raw string.

**Ordering is rebuilt, not repaired.** A change point is defined against the
previous snapshot, so an out-of-order arrival (backfill importing an older run
after a newer one) invalidates its neighbours. Such an arrival writes
**nothing** and marks the index dirty; the next read rebuilds. At 1.2–5.4 s a
rebuild is cheap enough that there is no incremental repair algorithm to get
wrong.

**A rebuild merges.** Snapshots get pruned and their rows must survive, exactly
as the curated index's do. A rebuild recomputes only timestamps whose snapshot
still exists and replays the retained rows in timestamp order — which both
keeps them and reconstructs the running "previous value". Without that replay
the oldest surviving snapshot would report all 8,000 of its leaves as having
just changed. **This is the only place in the feature where data can be lost**,
and it has three tests of its own.

`field_history` gains tier 0 between the curated index and the scan. The
eleven curated properties keep their tier — this adds a tier below them, it
does not re-route what already worked.

## The surface

`/param-history/changes` — a `Trends | Changes` tab strip on Param History.

Paged by **snapshot**, not by row: a regenerate rewrites thousands of
parameters at once, and a row-paged feed would spend its whole page on that one
event and hide every other. Each group shows its true count, the first 25 rows,
and a *Show all N from this snapshot*. Rows read `old → new (Δ)` through the
docs/76 delta, so a change reads the same here as in the Review tray. A path
opens its own timeline via the existing `/field/history` panel; the run that
made the changes opens with **Data**.

`/param-history/param-search` backs a typeahead — 11,000 paths is not a
dropdown.

Measured on the 264-snapshot chip: first page 113 ms (includes the lazy
freshness check), filtered 18 ms, older page 21 ms.

## Verification

Real snapshot stores, not fixtures:

* **Replay is the truth.** Applying every change point in order reproduces each
  chip's newest `state.json` exactly — 0 missing, 0 extra, 0 wrong across four
  chips (9,893 / 8,850 / 1,755 / 824 parameters, including pointer-resolved
  ones).
* **Incremental == rebuilt.** The capture path (one snapshot at a time) and the
  repair path (recompute from disk) produce identical rows and identical series
  on 500 sampled paths per chip.
* `tests/test_leaf_index.py` (36, incl. a corpus class that runs against a real
  store via `QSM_HISTORY_ARCHIVE`), `tests/test_param_history_changes.py` (18).

## Known limits

* `value` is REAL: the int/float distinction of docs/56 is not carried here.
* Booleans and strings are not parameters and are not indexed. A string-ified
  number is not either — docs/77's repair is what turns it into one.
* A pointer that resolves to nothing numeric still costs a 150-snapshot scan.
* The feed's per-row "previous value" is one small indexed query per row (N+1);
  at 25 rows × 20 groups that is a few ms, but it is not free.
