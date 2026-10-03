# docs/258: the history index never loses an Apply write

2026-10-03, branch `fix/history-index-lock`. Closes the "Deferred index lock"
open item of docs/250. History of the branch:

| commit | what |
|---|---|
| `1e3ef3bb` | the first fix (Codex) -- reviewed below, **reverted** |
| `eeb8db8e` | `git revert 1e3ef3bb` |
| (this commit) | the minimal fix + pins + this note |

## The bug

On a fresh chip the first Apply captures two snapshots (the backup and the
save) and indexes both on deferred background threads. Both reach the
brand-new `<instance>/history/<chip>/index.sqlite` at once and both bootstrap
it (`_ensure_param_history_schema`: `PRAGMA journal_mode=WAL` + CREATEs). The
loser's WAL switch fails:

```
WARNING Deferred index of snapshot ... failed; _ensure_index_fresh will heal on the next read
sqlite3.OperationalError: database is locked
```

Switching a rollback-journal file to WAL needs an exclusive lock, and SQLite
does **not** run the busy handler for it: with another connection holding
`BEGIN IMMEDIATE` on a fresh file, `PRAGMA journal_mode=WAL` with
`timeout=10.0` failed in **0.11 ms** here (1.83 ms in the Codex round). Once
the file is WAL, the same pragma is a no-op that succeeds even while a writer
holds the lock (probe: query `wal`, set `wal`, 0.32 ms).

The "heal on the next read" never reached the 🕘 popover:
`field_history`'s tracked-property tier read `param_history` without
`_ensure_index_fresh`, and any non-empty row set is taken as the whole
timeline (the leaf/scan tiers run only on zero rows). So one of the folder's
own values stayed missing. `column_history`'s tracked fast path (the column
🕘, and autofit's G5 drift gate, `routes.py` `history_points`) has the same
shape.

## The fix (`core/history.py`)

1. **`_index_write_lock(idx_path)`** -- one `RLock` per index file,
   process-wide (a plain dict keyed by the normalised absolute path; shared by
   every `HistoryManager`). Held:
   - by `_ensure_param_history_schema` (every bootstrap: capture, migration,
     backfill);
   - by `_index_snapshot_into` when it owns its connection, from the bootstrap
     **through COMMIT and close**. Bootstrap-only was the first version tried;
     its leaf assertion failed twice in ~25 runs, and the failure was real: after the
     backup's bootstrap released the lock, the save could reach
     `BEGIN IMMEDIATE` first, and the backup then became an *out-of-order*
     leaf ingest, which writes nothing and marks the leaf index dirty
     (`leaf_index.ingest_snapshot`), i.e. a full background leaf rebuild on the
     next leaf read. That inversion exists on the base too, on every Apply
     (pinned red on `d5fcf0d4`); holding the lock to COMMIT commits one
     process's writes to one file in lock order, and the backup's thread
     starts first.
   - by `_open_index`'s first-use bootstrap **only when the file is not WAL
     yet** (`PRAGMA journal_mode` is a header read). A reader's first open of a
     fresh file is a bootstrap like any other and failed the same way (or made
     the capture's fail); a reader of an established file switches nothing and
     never waits.

   Lock order: the sync capture path takes it while holding the manager lock;
   nothing under it takes the manager lock (schema DDL, the capture
   transaction, `leaf_index.ingest_snapshot`, `_bootstrap_open_index`), so
   there is no cycle. It never touches the web build lock or `bg_gate`
   (`wait_quiet` runs before `_index_snapshot_into`). Worst case unchanged in
   kind: a writer that used to wait on SQLite's busy timeout behind another
   in-process writer now waits on this lock for the same transaction.
   Writers in another *process* are not covered, exactly as before.

2. **`_index_fresh_for_read`** before the tracked-tier read of
   `field_history` and `column_history`: the existing curated self-heal
   (`_ensure_index_fresh`: join in-flight deferred writes, rebuild missing
   snapshots, memoised on the snapshot count), wrapped so a failing heal never
   costs the read (it falls through to the rows the index has, as before).

Nothing else changed: identity routing, capture/dedup, migrations, the leaf
gate, the RAM tables and every Trends/provenance path are byte-identical to
`d5fcf0d4`.

## Review of the Codex version (`1e3ef3bb`), and why it was reverted

It changed `history.py` by 415 lines plus `chip_trends_ram.py` and
`param_history_ram.py`, and edited three existing tests that pass on the base.

| Codex change | verdict |
|---|---|
| per-file write lock (WeakValueDictionary of RLocks) around bootstrap + capture transaction, and in the schema helper | **kept in substance** (re-implemented as 1. above) |
| `_open_index` rewritten to call `_ensure_param_history_schema` on every first open | **reverted**: took the write lock on the first open of *established* files too, so a page render's first read of a chip could queue behind a capture's whole insert. Now locked only while the file is not WAL (pinned) |
| `_ensure_index_fresh` on tracked `field_history` / `column_history` | **kept** (with a never-fail wrapper) |
| `_ensure_index_fresh` in `indexed_run_ids` | **reverted**: only feeds the auto-backfill residual counter, whose false positives are harmless by design (content-hash dedup, `routes.py` "RESIDUAL" comment) |
| `list_chip_histories` runs `_ensure_index_fresh(<chip dir>/quam_state)` for **every** chip dir | **reverted** -- this is what broke `test_chip_histories_rows_equal_cold_over_random_events` (below) |
| `_ensure_index_fresh`: skip-scan `COUNT` replaced by `SELECT DISTINCT timestamp` + set coverage, memo keyed on the snapshot-list object | **reverted**: a full scan of `param_history` after every capture, on every curated reader, to close a gap that needs pruned snapshots (auto-prune only above `max_snapshots`, default 100,000; deletes remove their rows). Pre-existing, not this bug -- see open items |
| `_ensure_leaf_index_fresh`: dropped the "no index file, nothing to repair" early return; coverage memo keyed on `str(self._history_dir(...))` + list identity | **reverted**: leaf tier, not this bug (the popover's leaf tier already gates through `leaf_field_series`) |
| leaf freshness gate added to `leaf_search`, `leaf_families` (the `fresh` flag ignored), `leaf_matching_paths`, `leaf_stats`, `ChipTrendsTable.families/leaf_families/leaf_matching_paths`, `param_history_ram.leaf_search` | **reverted**: inverts the RAM-first decision the Trends pins encode -- the curated Trends render and the provenance surfaces never run the leaf freshness gate (only the typeahead pays it). No numbers were offered that it never blocks a render |
| edits to `test_param_history_ram.py`, `test_trends_all_entities.py`, `test_trends_provenance.py` | **reverted**: all three restored byte-identical to `d5fcf0d4`, and they pass |

### What broke the memo test, with evidence

`test_chip_histories_rows_equal_cold_over_random_events` failed on Codex's
code at `assert hits >= 10` (9). Codex's explanation -- the three folders
named chip0/1/2 share a fingerprint and so are one history -- is *true on the
base as well*: probed on `d5fcf0d4`, all three route to `history/chip0` (an
unclaimed name adopts the existing fingerprint-matching dir, tier 1 of
`_resolve_from_ident`). Codex did not change routing, and the test passes on
the base with one history. Giving the fixture three hosts hid what the change
really did: the listing GET started **writing**. The test's event 1 simulates
another process committing (it deletes the newest timestamp's rows);
instrumented over the original 25 steps:

| | memo hits | `rebuild_index` calls from `list_chip_histories` | listing at step 0 / step 23 |
|---|---|---|---|
| base `d5fcf0d4` | 12 | 0 | `snapshot_count` 2 / 1 |
| Codex `1e3ef3bb` | 9 | 3 (steps 0, 10, 23) | 3 / 8 |

A read of `/param-history` re-ingested rows another process had removed,
synchronously, for every chip dir under the instance (archived chips nobody
opened included), reported its own repair instead of the committed index, and
by rewriting index files defeated the RAM P8 per-chip row memo the test pins.

## Pins (`tests/test_history_index_lock.py`, 9 tests)

The hooks never inject an error: they pause a real connection (at its WAL
switch holding a real `BEGIN IMMEDIATE` reservation, or just before its
`BEGIN IMMEDIATE`); every failure is the unmodified SQLite engine's.

On the base `d5fcf0d4`: **7 red, 2 green** (the two green ones constrain the
new code only: an established-file reader never waits, a failing heal never
costs the read).

| pin | what it holds |
|---|---|
| `test_fresh_chip_race_keeps_both_values_in_field_history` | the reported bug end to end: popover shows [2, 1], no failed deferred write |
| `test_concurrent_writers_commit_every_snapshot_without_read_repair` | both rows in `param_history` and `leaf_snaps`, read directly |
| `test_the_save_never_commits_before_the_backup` | lock held to COMMIT: no out-of-order leaf ingest, leaf index not dirty |
| `test_a_migration_bootstrap_waits_for_a_capture_bootstrap` | the schema helper itself is serialised |
| `test_a_readers_first_open_of_a_fresh_file_waits_for_a_capture` | `_open_index` bootstrap of a fresh file is serialised |
| `test_a_readers_first_open_of_an_established_file_never_waits` | ...and of a WAL file is not |
| `test_tracked_tier_reads_heal_a_lost_write[field/column]` | the two tracked tiers run the self-heal |
| `test_a_failing_heal_never_costs_the_read` | the wrapper |

Mutations (each applied to a scratch copy of the final tree, pin file run,
original restored; restored run 9/9 green):

| mutation | pins that went RED |
|---|---|
| M1 schema helper unlocked | migration bootstrap |
| M2 `_open_index` fresh-file branch unlocked | fresh-file reader |
| M2c `_open_index` always locks | established-file reader (blocked 5 s) |
| M3 registry not shared (a new lock per call) | race x2, order, migration, fresh-file reader |
| M7 capture lock released right after the bootstrap | order |
| M8 capture lock removed, schema lock kept | order |
| M9 both writer-side locks removed (= the base writer) | race x2, order, migration, fresh-file reader |
| M4 `field_history` gate removed | tracked[field] |
| M5 `column_history` gate removed | tracked[column] |
| M6 heal failure not swallowed | failing heal |

**10/10 mutations RED; every one of the 9 pins is killed by at least one.**

Stability: 10 sequential runs and 6 parallel runs, all 9/9.

## Tests

Selection: every `tests/test_*.py` whose text mentions `history`,
`param_history`, `leaf_index`, `index.sqlite`, `trends`, `field_history`,
`chip_trends_ram` or `param_history_ram` -- **111 files** (all top-level). Env
`cqt`, `PYTHONUTF8=1 ... -m pytest <files> -q --timeout=900
--timeout-method=thread -p no:cacheprovider`, run as 8 parallel shards on a
machine shared with other workloads:

- **3,795 passed, 31 skipped, 4 failed.** The 4 were
  `test_web.py::TestWorkspace::test_sidebar_filter_{by_date,multi_token_with_date,by_status}`
  (the workspace sidebar answered "No runs in this folder match" -- a scan under
  load) and `test_autosync_merge.py::...::test_the_shipped_client_behaves` (a
  jsdom selfcheck, client JS only). Neither touches `history.py`. All 4 pass
  in isolation on this branch AND on the base; the two whole files re-run
  alone on this branch: **634 passed, 19 skipped, 0 failed**.
- The three restored tests + the new pins, alone: `test_param_history_ram.py`,
  `test_trends_all_entities.py`, `test_trends_provenance.py`,
  `test_history_index_lock.py` -- **148 passed**. `git diff d5fcf0d4` on the
  three restored files, `chip_trends_ram.py` and `param_history_ram.py` is
  empty.

## Timings

Synthetic chip, built once with the base code and copied fresh for every
run: **200 snapshots, 20 qubits, 8,020 numeric leaves per state** (tracked T1,
T2ramsey, f_01, x180 amplitude + 400 custom leaves per qubit; every snapshot
changes several). Each variant ran in its own process with `PYTHONPATH` on its
tree; rounds interleaved base -> final -> Codex, twice. Shared machine (other
sessions' rigs and a QEC campaign were running), so differences under a few
ms are noise. Milliseconds, two rounds shown as `r1 / r2`.

| read | base `d5fcf0d4` | final | Codex `1e3ef3bb` |
|---|---|---|---|
| 🕘 `field_history` tracked T1, cold (median of 5 new managers) | 30.0 / 20.8 | 33.8 / 34.5 | 42.1 / 39.3 |
| 🕘 tracked T1, warm median (51 calls) | 8.11 / 5.58 | 7.11 / 7.24 | 5.99 / 5.47 |
| 🕘 tracked T1, warm p95 | 9.09 / 6.78 | 8.86 / 7.99 | 8.49 / 8.40 |
| 🕘 leaf `custom.k0`, warm median (path unchanged) | 9.90 / 9.38 | 8.74 / 8.99 | 7.34 / 8.78 |
| 🕘 first read after a SYNC capture, median of 10 | 29.7 / 27.2 | 39.8 / 33.9 | 39.9 / 34.9 |
| 🕘 first read after a DEFERRED capture (an Apply), median of 10 | 33.6 / 30.0 | 151.8 / 129.6 | 108.4 / 111.9 |
| ... showing the value just applied | **0/10 / 0/10** | 10/10 / 10/10 | 10/10 / 10/10 |
| Trends `/topology/trends?metrics=f_01`, first render | 695 / 699 | 909 / 629 | 741 / 582 |
| Trends f_01, warm median (10) | 2.2 / 1.9 | 2.8 / 1.7 | 2.4 / 1.5 |
| Trends `metrics=T1`, first / warm median | 17 / 2.0, 17 / 1.9 | 22 / 2.8, 14 / 1.6 | 17 / 2.1, 14 / 1.8 |
| `list_chip_histories`, first call (1 chip dir) | 5.7 / 5.7 | 7.4 / 4.8 | **929 / 927** |

Reading it:

- Warm popover reads and the Trends render are unchanged within noise; the
  Trends route's code is byte-identical to the base.
- Cold popover reads pay the one-time curated check (schema verification
  open + count), +5..14 ms.
- **The read right after an Apply** is the bug's own window. The base answered
  in ~30 ms with the value just applied missing **10 times out of 10** (the
  deferred write had not landed; when it is lost, it stays missing). The fix
  waits for the deferred write (`_join_deferred_index`) and answers it. A
  breakdown (two more runs each): read 121.9 / 122.7 ms of which the join is
  80.5 / 79.3 ms against a 122 ms deferred write; Codex 119.0 / 124.6 ms, join
  74.5 / 78.7. No `rebuild_index` in any of them. The final-vs-Codex gap in the
  table above was noise.
- The count gate the popover now shares runs once per capture per chip (memo
  on the snapshot count, shared with every other curated reader). Its query
  at **10,000 snapshots / 2.4M rows** (312 MB index, median of 5 cold
  connections): base skip-scan `_ph_snap_count` **167.5 ms**; Codex's
  `SELECT DISTINCT timestamp` **575.1 ms**.
- **Codex's listing**: 0.93 s for ONE chip dir, **4.7 s for five** (base:
  41 ms). Profile: `_ensure_index_fresh(<chip dir>/quam_state)` pushes a
  synthetic path through the identity ladder, and `identity_of` reading a
  state.json that does not exist spends ~0.92 s per dir in `safe_io`'s retry
  ladder. The identity cache then holds the miss, so this is paid once per
  chip dir per process (a 30-chip instance: ~28 s on the first
  `/param-history`); a later listing after a capture cost 3.1 ms against the
  base's 0.6 ms -- plus any synchronous `rebuild_index` it decides on (the
  memo-test table above).

## Open items

- **Cross-process writers** (two SM windows on one chip) are still serialised
  only by SQLite; a fresh file bootstrapped by two processes at once can still
  lose the WAL race. Rare (both must hit a brand-new index), unchanged here.
- **Count masking by pruned rows**: `_ensure_index_fresh` compares counts, and
  `param_history` keeps the rows of auto-pruned snapshots, so on a chip past
  `max_snapshots` a lost write can hide behind pruned rows. An incremental
  per-timestamp coverage check (only timestamps not yet verified, point
  lookups on the PK) would close it without a full scan; separate change.
