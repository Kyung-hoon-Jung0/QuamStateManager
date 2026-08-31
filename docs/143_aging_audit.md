# docs/143 — the aging audit: months of accumulation, measured then excluded from the hot paths (2026-08-31)

The user's question, paraphrased: *researchers keep ONE data folder for
months, restarting SM over and over — does anything accumulate on disk
(cache, history) that makes SM slower over time? If nothing does, implement
nothing. Old data must never be auto-deleted — it must be auto-EXCLUDED from
loading and checking (lazy, or hashed).* Justification was to be judged
FIRST.

## The verdict: justified, with a smoking gun

A months-old instance was synthesized (one chip, **10,000 snapshots** under
`instance/history/<chip>/` in the exact on-disk shape HistoryManager writes,
+ **2.4M rows** in its `param_history` index — 617 MB) and every hot path
measured cold, as a fresh process sees it:

| path | measured at 10k snapshots | when it is paid |
|---|---|---|
| `list_snapshots` cold | **6.5–9.5 s** (one meta.json open per dir) | first history touch of EVERY session, and again after EVERY capture/ingest invalidation — i.e. per run while the app is open |
| `extract_property_history`, 3 props | **21.9 s** (50.4 s for 12 props) | ChipTrends section open, cache invalidated per new snapshot |
| `index_summary` cold | 8.1 s (6.5 s of it = the list_snapshots above) | /param-history render |
| `history_disk_stats` | 8.5 s | /param-history render |
| `field_history` after fresh | 0.14 s | fine — left alone |

Two structural causes, both textbook accumulation: the snapshot LIST is
re-derived from ten thousand small files, and the trend query's window
functions (`ROW_NUMBER()/COUNT(*) OVER`) **scan every matching row of the
accumulated table per render** — returning only changed rows via `LAG` is
just as slow (20.5 s for 4,100 returned rows), because the scan itself is
the cost. No SQL phrasing fixes that; the data organisation must.

## What shipped (nothing deleted; old rows leave the hot checks)

**① `param_history_cp` — the change-point companion** (+ `_cp_last`,
`_cp_meta`), living in the same index.sqlite:

* Contents per (qubit, property): the series' first point + BOTH edges of
  every value transition (the docs/142 step-shape rule); `_cp_last` holds the
  rolling newest point. Equality = exact with NaN normalised.
* Maintained by **rowid watermark** at read time (`_ensure_cp_fresh`):
  every INSERT — REPLACE included — grows `MAX(rowid)`, so the delta since
  the stored watermark is exactly the new rows, O(new). A delta row whose
  timestamp is not strictly newer than its partition's `_cp_last` (an
  out-of-order backfill import, or an in-place REPLACE — a replaced key can
  never out-timestamp the partition max) rebuilds THAT partition from its
  own index-ordered slice. Deletions cannot move a watermark: the two
  `DELETE FROM param_history` sites (`rebuild wipe`, v3-migration move) call
  `_cp_invalidate` in the same transaction. A busy index returns "not
  usable" and the caller falls back to the windowed SQL unchanged.
* `extract_property_history(compress="changes")` with **no since/until/
  trigger filter** — exactly the shape of both compress consumers
  (ChipTrends curated tier, topo sparkline popup) — reads cp ∪ cp_last
  instead of running the windowed SQL. Filtered calls fall through
  unchanged.
* Self-migrating: `_ensure_cp_fresh` creates its tables on a months-old
  index (CREATE IF NOT EXISTS), so existing customer DBs need nothing.
* Measured: warm read 16–22 s → **2.4 s** (fresh process; the remainder is
  meta-stat validation + index freshness, not the scan); after one new
  snapshot **3.7 s** (delta apply); one-time full build 20 s on first
  compress read of an aged chip (batched — the first per-row implementation
  took 47 s and was rewritten). **Exact-equivalence proven** against the
  unthinned (`downsample=None`) windowed path; note the THINNED windowed
  path was itself slightly wrong (SQL stride pre-thinning could pick a
  non-adjacent flat edge) — the companion is more correct, not just faster.

**② The snapshot-list manifest** (`snapshots_manifest.json` per chip dir):
`_list_snapshots_in_dir` records every parsed meta keyed by dir name with
the meta.json `(size, mtime_ns)` signature; a scan stats each meta.json
(cheap) and re-parses ONLY new dirs or dirs whose meta.json moved (label/pin
edits rewrite it in place — pinned). Maintained by the READER, no writer
coupling; any doubt is a per-entry miss; rewritten only when something
changed. Cold 7.4 s → **1.8 s**, and — the part that matters for a
months-open app — the per-run invalidation re-scan now re-parses one dir,
not ten thousand. `index_summary` rides the same fix (8.1 s → ~1 s).

Pinned by `tests/test_aging.py` (11) — companion correctness (first/edges/
last, incremental append, out-of-order partition rebuild, REPLACE
visibility, delete-site invalidation, filtered-call bypass agreement,
multi-qubit isolation) + manifest (round trip, delta on add/remove,
**in-place meta edit picked up**, corrupt manifest = miss). Mutation-verified
4/4 red: out-of-order never rebuilding, `_cp_invalidate` as a no-op, the
manifest ignoring meta edits, the fast path dropping the rolling last.
Regression: the 12 history-consumer test files = 439 passed.

## Audited and deliberately left (recorded so nobody "fixes" silently)

* **Snapshot COPIES on disk** (~617 MB at 10k synthetic; ~2.5 GB at real
  state sizes): disk, not speed — and auto-deleting violates the user's
  rule. Pruning exists, pinned-exempt, user-initiated.
* **leaf_index dirty rebuild** (re-reads every snapshot; ~10–15 s at 5k,
  worse at 10k) — fires only on out-of-order arrival; known open item from
  docs/83/127, unchanged here.
* **The alignment scan** (~4–5 s per fetch at 5k runs) — off the critical
  path since docs/142 C (lazy fragment); making the scan itself incremental
  is its own round.
* **The docs/142 listing cache** grows ~0.4 KB/run — loads in ~0.1 s at
  10k; not an accumulation hazard.
* Bounded by construction, verified by reading: undo journal (200),
  backfill failure capture (50), `_data_json_cache` (LRU 200), tree HTML
  memos (version-keyed), working copies (GC with peer protection).
* A test-fixture lesson: writing an EMPTY state.json into a synthetic
  snapshot rerouted the whole chip through the docs/20 identity ladder to an
  `_alt_unknown` dir mid-benchmark — the ladder working as designed; the
  "bug" was the fixture. Benchmarks against identity-laddered stores must
  use real content.
