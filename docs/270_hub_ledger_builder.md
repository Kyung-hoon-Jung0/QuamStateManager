# 270: Offline saved-state ledger and builder

S3 of the state-tracking hub, implemented on `feat/hub-ledger-builder`.
Design source: `D:\work\study\2026-10-03_state-tracking-hub\DESIGN.md`, section 3,
read on 2026-10-04. The task's binding decisions and the executable S2 contract
in [269](269_hub_rules.md) take precedence. No SM server, routes, search index,
or SM-event `record()` was added. Existing tests were not edited.

## Public entry points

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
& D:/miniconda3/envs/cqt/python.exe -m quam_state_manager.core.hub_build `
  <archive-root> --out D:/work/sm_qa_rigs/hub3/<ledger-directory> [--limit N]
```

`hub_build.build(root, out, limit=None)` returns discovery, additions, extra
locations, errors, **deferred** (review fix: an unreadable newest run is not
committed), raw-hash repeats, event/row totals, archive offset, and elapsed
seconds. `limit` counts **new events this invocation**, so repeated limited builds
make progress. A completed location is authoritative for this offline,
immutable-archive projection; it is not rehashed on every restart.

```python
from quam_state_manager.core.hub_store import HubStore

with HubStore(ledger_directory) as ledger:
    document = ledger.state_at(eid)
    rows = ledger.diff(eid_a, eid_b)  # S2.Change objects, sorted by holder path
    event = ledger.event(eid)
```

An unknown eid raises `KeyError`. An error event raises `ValueError` from
`state_at`: it has no saved document, and returning a previous run's state as
its own would be misleading. Error events remain in the timeline and advance
the build watermark. Later runs compare against the last **successful** head.
An error event keeps the node's own `status` (review fix: 18 real runs that
*finished* without saving state were stored as `status="error"`); the reason the
ledger has no state is in `error`.

## Schema as built

`ledger.sqlite` uses WAL, foreign keys, `synchronous=NORMAL`, and a 16 MiB SQLite
page-cache budget. It is a rebuildable projection; no primary SM event journal
is written. Each event, changes, locations, new blobs, optional checkpoint, and
watermark commit in one transaction. Rollback restores the path-id cache too.

| Table | Columns and constraints |
|---|---|
| `events` | `eid INTEGER PRIMARY KEY AUTOINCREMENT`; `kind`; integer `t_utc_us`; `t_src`, `t_quality`; unique `ord REAL`; `root_id`, `rel_path`, `run_id`, `experiment`, `status`; integer `run_start_us`, `run_end_us`; JSON `parents`, `targets`; `patches_n`; `actor`, `plan_id`, `src`; `state_hash`, `base_hash`, `state_ref`; `n_changes`, `flags`; **`shape_hash`, `error`**. |
| `paths` | `pid INTEGER PRIMARY KEY`, unique literal holder `path`, `entity`, `entity_kind`, `family`, `src_file`. Entity/family are derived hints; `src_file` remains null because S3 records the merged holder. |
| `changes` | `pid`, `eid`, integer `op`, `num REAL`, `txt`, `old_num REAL`, `old_txt`, `proven`; primary key `(pid,eid)`, `WITHOUT ROWID`; index `(eid,pid)`. |
| `blobs` | SHA-1 `hash PRIMARY KEY`, `gz BLOB`; deterministic gzip (`mtime=0`) of canonical UTF-8 JSON. Reads verify the uncompressed hash. |
| `checkpoints` | `eid PRIMARY KEY`, `hash`; full merged head every **250 events**, including zero-change and error events in the cadence. An error checkpoint contains the unchanged successful head. |
| `roots` | `root_id PRIMARY KEY`, unique normalized absolute `path`, SHA-1-derived `folder_key`, `offset_hint`. |
| **`locations`** | `eid`, `root_id`, `rel_path`; primary key `(root_id,rel_path)`, `WITHOUT ROWID`; index on `eid`. Archive copies add locations without adding events. |
| `meta` | `k PRIMARY KEY`, `v`; `schema_version=1`, `rule_version=269-1`, `checkpoint_interval=250`, optional `chip_identity`, and JSON `watermark:<root_id>` containing `eid`, `t_utc_us`, `rel_path`. |

The run identity unique index covers `(t_utc_us, run_id, experiment, state_hash)`;
SQL null hashes use one explicit index sentinel for unavailable pairs. The
original pair hash is **S2.state_hash**, SHA-1 of state bytes + NUL + wiring bytes,
including whitespace. It is distinct from canonical array/shape/checkpoint
addresses. `eid` is insertion identity, never run id. S3's `ord` is an integral
append rank stored in the design's REAL column, not a floating UTC timestamp.

Operations: `set=0`, `add=1`, `gone=2`, `retarget=3`. Absent sides have both
payload fields null; JSON null is text `null`. Finite ordinary numbers use REAL.
NaN, infinities, and integers outside REAL's exact integer range (`abs(n)>2**53`)
use JSON text on **both old and new sides**, preserving nonfinite values and
arbitrarily large integers. Booleans and strings always use canonical JSON text.
Long arrays retain S2's `{"_array": length, "_hash": sha1}` in `txt`, with the
normalized scalar-list payload in `blobs` under that exact hash.

Flags implemented here: `REVERTS_TO_EARLIER=1`, `TIME_ASSUMED=2`,
`CHIP_UNCERTAIN=4`. Other SM-event flags belong to S4. The existing chip identity
ladder supplies name/fingerprint evidence. Missing evidence or a conflicting
identity is retained and marked uncertain; S3 does not silently drop or route
foreign runs into another ledger. Routing requires S5's chip/root registration.

## Build and replay algorithm

1. Inspect real dated folders matching `<root>/<YYYY-MM-DD>/#<id>_<name>_<HHMMSS>`.
   Read `node.json`, retaining only needed metadata, patches, parents, and targets.
   Standard `quam_state/{state,wiring}.json` wins; older alternate state folders
   are also accepted. Missing/malformed node or state files produce error events.
2. Infer the majority offset with the existing time primitive. Resolve each
   instant through **run_time.resolve**, with that hint and the already-read node
   evidence; sort by instant, root folder key, run id, then experiment/folder for
   deterministic ties. Fractional microseconds and explicit offsets survive.
   Naive run bounds use the same archive hint. No run DAY is computed.
3. Skip completed locations. Read an unknown state/wiring pair with size/mtime
   checks before and after the read, retrying a changing pair up to three times.
   Use the run identity to admit alternate locations. Otherwise keep an event,
   except that the **newest** discovered run with no readable pair is deferred
   (review fix): it may still be in flight, and a committed location is never
   revisited. Once a later run exists, a stateless run is final.
4. If the raw pair hash equals the successful head's hash, write a zero-change
   event **without parsing the state/wiring bytes**. Metadata still belongs to
   this run. If it differs, use S2 `merged`, `flatten`, and `diff` against the head.
   Raw-byte changes can also produce zero rows under `same`, such as `1 -> 1.0`.
5. Store S2 old/new rows unchanged in meaning. `proven=1` requires an explicit
   add/replace leaf patch whose value matches the saved leaf under S2 `same`;
   long-array patch values pass through S2 flattening too. JSON Pointer patch
   segments are decoded; `/quam` is the archive patch envelope, not a holder.
6. Persist array payloads and a deduplicated container skeleton. Skeleton leaves
   are null placeholders; dict/list structure, escaped keys, numeric dict keys,
   and empty containers survive. S2's path escaping and array scalar normalization
   helpers are reused. Write a full merged checkpoint at each 250th event.
7. Commit and advance that root's watermark. Resume reconstructs the successful
   head from the ledger, scans locations to catch additions, and preserves eids.
   Watermarks are progress records, not a shortcut that discards older folders.

`state_at(eid)` loads the nearest checkpoint by ledger order, flattens it through
S2, replays subsequent change rows, then fills the target event's skeleton and
loads its array blobs. It needs **no archive files**, including before the first
checkpoint. At most 249 subsequent event intervals need replay. `diff(a,b)` uses
S2 on the two reconstructed documents. Int/float-only representation changes
and array normalization replay under `same`, rather than promising original
byte or numeric-type identity. Empty containers and container types do survive.

No worker pool is used. The builder retains metadata plus the current and next
documents/flat maps, not every run state. Source files are never opened for write.
Newly arriving folders wait for the next discovery. An unknown run preceding
the ledger head fails explicitly before writing it; S3 does not generate a diff
against the wrong neighbor. General late insertion and overlapping-root merging
are required S5 work, while copied roots already deduplicate correctly.

## Decisions versus DESIGN

| Design proposal | Implemented decision |
|---|---|
| Detect `OFF_LIVE` and diff the next run against an earlier A | **Dropped by the user.** Every successful run compares against the head. A -> B -> A records both transitions; no copied-state inference or fit/human split. |
| Hash equal to a non-adjacent earlier state | Keep `REVERTS_TO_EARLIER` as a fact; adjacent repeats do not get it. |
| Resolve pointer holders and emit target-induced rows | Preserve the binding S2 raw-holder contract. Retargets are recorded; unchanged pointer strings create no derived holder rows. |
| Long scalar arrays | One S2 marker and a content-addressed gzip payload, including bool/string/null arrays. |
| Checkpoints alone plus scalar changes | Add shape blobs so empty containers and dict/list identity replay without source access. |
| Root included in historical run identity concerns | Binding identity is `(instant, run_id, experiment, raw state hash)`; additional roots/paths are locations. |
| Process pool, local late insertion, live routing | Sequential bounded-memory offline build; late insertion and chip routing are explicit S5 obligations. |

## Required real-data checks

Exact machine-readable results are in
[270_hub_builder_checks.json](270_hub_builder_checks.json).
`tools/check_hub_builder.py` accepts explicit archive paths, `--scratch`,
`--history-copy`, and `--report`; its subprocess monitor uses the installed
`psutil`. All ledgers, synthetic folders, copied history, build logs, mutation
logs, and pytest temporaries are under **`D:\work\sm_qa_rigs\hub3`**. Production
history was read only for check (b), and customer archives were read only.

### (a) Replay equality

Seed 270 selects 500 distinct successful runs per archive, plus every checkpoint.
Every comparison independently reads that run's saved pair and compares flattened
merged states with S2 `same`. Missing/corrupt saved states are error events, not
eligible equality samples. The p50/p95 timing encloses only `state_at`, including
its SQL, checkpoint/shape decompression, replay, array loads, and reconstruction.

| Archive | Random runs | Checkpoints | Unique checks | Mismatches | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|---:|
| KH_202608_CZ | 500 | 16 | 516 | **0** | 32.922 | 50.427 |
| Novera9Q | 500 | 8 | 507 | **0** | 8.014 | 17.218 |
| arbel_20260929 | 500 | 6 | 503 | **0** | 45.188 | 76.550 |
| Synthetic 10,000 | 500 | 40 | 539 | **0** | 35.036 | 107.222 |

Second builds over each frozen input set add **0 events and 0 change rows**.
The live archive grew from 1,600 to 1,602 runs during the first replay: the next
scan correctly caught two new events/four rows. The idempotence audit was then
fixed to freeze the original input set; new arrivals are catch-up, not duplicates.
The live replay table includes those two caught-up runs in its eligible population.

### (b) Coverage of today's copied history

Filesystem-copied `instance/history/KRISS_CZ/index.sqlite` and any WAL were opened
**only in scratch**, never through a live SQLite connection. That copy contains
four `leaf_snaps`, **987** `leaf_cp` points, all referring to runs in the first
archive. The corresponding snapshot state/wiring/meta files were copied read-only
into scratch to investigate the six initially unmatched points.
The copied index already has `dirty=1`, with the existing reason
`out-of-order 20260907_123829_142 < 20260907_124116_146`; it was not rebuilt or edited
in production. Its content digest and kind counts are recorded in the JSON report.

**Covered 16 / explained by binding rules 971 / missing 0.** This is coverage
modulo the binding rules; it is not a claim that all 987 sparse-history rows became
literal change rows at those runs.

| Classification | Points | Evidence |
|---|---:|---|
| Direct same-run, same-path, same-new-value change row | 16 | Compare the SQLite row payload under S2 `same`. |
| Unchanged versus immediate predecessor run | 575 | The event's saved value equals the legacy point and its preceding run. Sparse history emits an initial baseline or catch-up row; run-by-run diff emits no row here. |
| Raw pointer holder contract | 385 | The stored raw pointer is present; the existing legacy walker reproduces the point's resolved number or unresolved-pointer kind. Target-induced holder rows are excluded by S2. |
| Already absent versus immediate predecessor | 5 | Path absent on both sides; sparse history's deletion already occurred at an earlier run. |
| Saved post-patch state versus legacy pre-patch copy | 6 | Two direct values plus four resolved-pointer values at run #146, with exact old/new patch evidence below. |

The six investigated points are real differences between the legacy snapshot and
the archive's own saved run, not builder omissions. For run #146, the copied
snapshot differs from the saved state at **exactly two holders**:

| Holder suffix | Snapshot / `patch.old` | Saved run / `patch.value` |
|---|---:|---:|
| `x180_DragCosine.amplitude` | 0.4971822358141066 | 0.9512200201208982 |
| `x90_DragCosine.amplitude` | 0.2485911179070533 | 0.4756100100604491 |

Both old and new values are proven against `node.json.patches`. Four legacy
relative-pointer holders resolve to those same two old values in the copied
snapshot. The hub records the **saved new values** at #146, as required, without
fabricating old-valued rows or moving them to another run. The audit permits this
explanation only when every snapshot-versus-saved difference is proved on both
old and new patch sides. Unexplained missing examples are empty; no points were
excluded for absent archives. Long-array and int/float exceptions count zero in
this particular four-snapshot copy.

### (c) Full builds: time, size, and peak RSS

Fresh ledger directories, one builder subprocess at a time; no pool. Timings
include discovery, hashing, parsing changed states, projection writes, checkpoints,
and WAL truncation. MiB is bytes / 2**20. RSS is monitored every 100 ms and includes
Windows' process peak working set. The process-start overhead is separately
recorded in JSON. These are measured desktop runs, not a controlled cold-cache
benchmark; other jobs were running.

| Input | Events | Error events | Change rows | Ledger MiB | Build s | Peak RSS MiB |
|---|---:|---:|---:|---:|---:|---:|
| KH_202608_CZ | 4,121 | 9 | 14,277 | 6.262 | 80.542 | 67.19 |
| Novera9Q | 2,082 | 13 | 13,395 | 3.332 | 20.675 | 60.27 |
| arbel_20260929, frozen at discovery | 1,600 | 1 | 26,607 | 5.137 | 65.382 | 61.11 |
| Synthetic concatenation | 10,000 | 27 | 38,205 | 12.551 | 209.166 | 85.99 |

All discovered runs became events, including incomplete pairs (none of the
three archives' newest run lacks a pair, so the review's deferral changes no count). The synthetic
archive concatenates retimed copies of the 4,121-run first archive to reach
10,000 distinct run identities. It copies each payload into scratch, then creates
hardlinks **only among scratch files**, rotating backing copies every 500 runs to
respect NTFS's link limit. Customer files are never hardlinked or edited. Its
27 error events are the repeated incomplete source runs. Synthetic preparation
is separate from build time. Peak builder memory remained below **86 MiB**, well
below the requested approximately 2 GiB ceiling.

## Tests, pins, and mutations

The new files are `tests/test_hub_store.py` and `tests/test_hub_build.py`, using
generic small fixtures. **29 new tests pass**. Focused regression, including S2
and the one-instant contracts, passes **180 tests**. The existing conftest
invalid-escape SyntaxWarning is unchanged. No full suite was run.

| Pin | Injected failure |
|---|---|
| WAL/schema/version compatibility | Use DELETE journal; bypass incompatible-version guard. |
| Gzip blobs, addresses, integrity | Use SHA-256 address; omit read-time hash verification. |
| Lossless nonfinite/large numbers, null versus absent | Encode exceptional numbers as null. |
| Long arrays, shapes, escaped paths, source independence | Reconstruct arrays as zeros; remove shape key escaping. |
| Checkpoint cadence, replay, arbitrary-eid diff | Shift checkpoint cadence; skip replay after checkpoint. |
| Atomic projection rollback and path cache | Remove the event transaction. |
| Error state lookup | Return an error event's unchanged head as its own state. |
| Offset-aware ordering and microseconds | Sort by run id before instant. |
| Archive hint and assumed-local flag | Discard majority offset hint. |
| Run-end fallback and metadata | Preserve invalid created_at as time source. |
| Identical raw hash, event retained, no state parse | Parse every identical pair. |
| Numeric equality despite different raw bytes | Diff against empty state. |
| Plain head diff and non-adjacent revert fact | Omit revert flag; diff the revert against empty state. |
| Copied-root extra locations | Disable run-identity lookup. |
| Identity includes raw state hash | Deduplicate runs while ignoring state hash. |
| Identity includes instant, id, experiment | Independently ignore each identity component. |
| Checkpoint replay and shared deep merge | Ignore wiring when merging. |
| Resume after interruption, watermark, idempotence | Write watermark under the wrong key. |
| CLI and limit counting new events | Admit one event past the limit. |
| Missing and corrupt states retained; subsequent base | Drop every error run. Both damage fixtures go RED. |
| Chip uncertainty and exact patch proof | Remove uncertainty flags; accept mismatching patch values. |
| Late unknown run rejected before wrong diff | Omit the chronological guard. |
| Stable pair reads | Accept changing source bytes. |
| Long-array patch proof follows S2 | Drop flattened patch array value. |
| Naive run bounds share archive offset | Resolve run_end without the archive hint. |
| Frozen discovery and next-build catch-up | Extend discovery while consuming it. |
| Declared chip, conflicting identity retained, repeated uncertainty | Remove uncertainty flags. |
| Error checkpoint contains successful head; subsequent replay | Skip checkpoints on error events. |

`tools/mutate_hub_builder.py` performs sequential source mutations, runs the
targeted pin with the mandated interpreter/pytest flags, logs failures in scratch,
and restores both source files byte-for-byte in `finally`. Syntax/collection
errors do not count as RED. Exact results are written to
`docs/270_hub_builder_mutations.json`. **36/36 mutations RED; all 28/28 test
functions covered**, including both missing/corrupt-state cases. Both source files
were restored byte-for-byte before the final focused regression.

Final focused command:

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
& D:/miniconda3/envs/cqt/python.exe -m pytest -p no:cacheprovider --timeout=900 `
  --basetemp D:/work/sm_qa_rigs/hub3/pytest-final `
  tests/test_hub_store.py tests/test_hub_build.py tests/test_hub_rules.py `
tests/test_one_run_instant.py tests/test_run_instant.py tests/test_run_instant_callers.py -q
```

Final result: `180 passed, 1 warning in 22.70s`.

## S4 and S5 obligations

S4 must add a durable `events.jsonl` primary journal and `record()` at every SM
write door, fsync before success, preserve actor/plan/source-run/undo provenance,
and project through the same S2 and store encoding. It must retain full post-state
blobs for SM events, avoid loss of large writes, define genesis/base ordering and
SM/run ties, teach catch-up to accept SM heads with no root location, and add
overlap/undo flags with a versioned bit assignment. Test
every write door and enumerate callers to catch an unrecorded writer.

S5 must register all roots for a chip, route positively identified foreign runs,
and insert late events in canonical order while repairing successor diffs,
base hashes, revert facts, order ranks, and affected checkpoints atomically.
It must retry incomplete/changing/error locations, handle evidence that acquires
an offset later, define relocation/source-mutation repair, and catch up every
registered root on chip open without a page visit. The S3 location map and
watermarks provide restart progress, but do not replace this reconciliation.
Frozen discovery's next-invocation additions already work offline.

Open questions for later steps: policy for repaired error-event identity and
explicit mixed-chip registration, and a compatible SM-event flag/order version.
No user decision blocks this offline S3 implementation.

## Review (2026-10-04, independent)

Adversarial review of `9bdaad77` before merge. **Verdict: merge after the fixes in
`8c984bbb`.** No P0 was found: no change is missing from the ledger, and no change is
attributed to the wrong run by the builder. Two P1 wrong-row generators and two P1
pin gaps were fixed. One P1 premise problem remains for S5 (run folders are not
immutable). Machine-readable results are in
[270_hub_builder_review.json](270_hub_builder_review.json).

The review used its own scripts and did not reuse `tools/check_hub_builder.py`. The
scripts and their outputs are kept in `D:\work\sm_qa_rigs\hub3\review\scripts` and
`D:\work\sm_qa_rigs\hub3\review`. The
ledgers were rebuilt from `9bdaad77` into `D:\work\sm_qa_rigs\hub3\review`. The
production `KRISS_CZ` history was copied before it was opened; its `index.sqlite` is
byte-identical (sha256 `b28c97cf…`) to the copy Codex used.

### (b) recomputed: what the hub holds, run by run

**Method.** The review script opens the ledger read-only. It folds the **change rows
itself** from genesis, without `state_at` or checkpoints, because those rows are what
every history surface will read. It rebuilds each event's document from the event's
shape blob and its own fold, then runs the legacy walker (`leaf_index.numeric_leaves`)
over that document.

For every legacy change point at snapshot run S, it finds the run at which the hub's
legacy-visible value of that path last changed, and the kind of hub row that caused
it. The window is the runs after the previous legacy snapshot, up to and including S.

At every snapshot run it also checks three things:

- **As-of maps, both directions.** The legacy index's value for every path known at S
  is compared with the hub's value there.
- **Legacy view.** The legacy walker over the hub's document is compared with the
  walker over the archive's own raw pair.
- **Folded rows.** The review's fold is compared with `S2.flatten(S2.merged(raw pair))`.

**Identity and time key.** Each of the 4 legacy snapshots was matched to its hub event
in two ways. By folder, it maps to a location row. By key, the legacy `ts` equals
`run_time.snapshot_key(t_utc_us, run_id)` of exactly one hub event (the docs/262 UTC
key). Both ways give the same event, with the same run id and experiment:

| ts | run | eid | `t_src` |
|---|---:|---:|---|
| `20260907_084348_015` | 15 | 15 | `2026-09-07T17:43:48+09:00` |
| `20260907_085348_022` | 22 | 22 | `2026-09-07T17:53:48+09:00` |
| `20260907_124116_146` | 146 | 146 | `2026-09-07T21:41:16+09:00` |
| `20260907_130429_147` | 147 | 147 | `2026-09-07T22:04:29+09:00` |

There is no off-by-run or offset slip.

| Where the hub records the legacy change point | Points | Hand-checked |
|---|---:|---:|
| Same run, row on the path itself | 16 | 16 |
| Earlier run, first window (#1-#15): legacy baseline at #15; hub `add` at genesis (823) or a `set` at #11-#14 (17) | 840 | 25 |
| Earlier run inside the window, row on the path | 89 | 25 |
| Earlier run inside the window, `gone` row (`ports.mw_inputs.con1.3.1.*`, run #48) | 5 | 5 |
| Earlier run inside the window, pointer target's row (`x180_DragCosine.length` 40->64, run #105) | 25 | 25 |
| **Hub newer than legacy at #146**: x180/x90 amplitude and 4 pointers to them | 6 | 6 |
| **Hub changed one run earlier than legacy**: the same 6 paths, seen at #147 | 6 | 6 |
| Missing | **0** | |

The as-of comparison differs at exactly those 6 paths, at #146 only. There are 0
reverse misses (paths the hub changed that the legacy index lacks). Legacy-view and
folded-row equality hold at all 4 runs.

The 12 marked points have one cause: the archive's #146 folder was **rewritten after
the run**. Its `node.json` and `quam_state/state.json` have mtime `22:04:08`, 23
minutes after `created_at`. The legacy snapshot was captured at about 21:47, from the
pre-acceptance state. The later rewrite carries the accepted patch:
`x180_DragCosine.amplitude` 0.4972 -> 0.9512, `proven=1`. The hub therefore records
the change at #146, the run whose patch it is. The legacy index first saw it at #147.

Codex's table was right that nothing is missing. It mislabelled the #147 half of
this pair: the 2 direct points and the 4 pointer points at #147 sit inside "unchanged
versus predecessor: sparse catch-up" and "raw pointer holder". In fact they are the
same deferred acceptance, seen from the next run. Codex's "unchanged" and "pointer"
buckets also only checked the value at S. This review checked that a hub row inside
the window produced that value.

**Hand checks.** At least 20 points were sampled per category, or every point when a
category has fewer. 108 real points were checked, plus 225 sparse and 275 dense
synthetic points. Each check reads the **raw archive pair** at three runs: the run
where the hub says the change happened, its predecessor, and the snapshot run. It
then confirms three things:

- the value moved at that run;
- it equals the legacy value;
- the hub's own row at that run is the one that moved it.

For long arrays, the check loads the blob named by the marker row and compares
element `i`. Every `gone` sample has the hub `gone` row, or the array or ancestor row,
that removed it. Result: 0 unexpected.

**Extended (b).** The 987 real points touch only 4 runs and contain no long-array
elements. To cover more, the review built a legacy index from the archives' own saved
states. It runs `leaf_index.numeric_leaves` and `_diff_rows` over every successful run
in hub order, keeping "latest" in RAM as `_latest_values` would. On the 183-snapshot
sparse case this replica was identical to real `ingest_snapshot`.

| Legacy index built from | Change points | Same run | Earlier run in window | As-of / reverse / legacy-view / folded-row mismatches |
|---|---:|---:|---:|---:|
| KH, every 40th run (183 snapshots) | 122,800 | 5,540 | 117,260 | 0 / 0 / 0 / 0 |
| KH, every run (4,112) | 1,030,999 | 1,030,999 | 0 | 0 / 0 / 0 / 0 |
| Novera, every run (2,069), -04:00 | 14,058 | 14,058 | 0 | 0 / 0 / 0 / 0 |
| arbel, every run (1,610) | 33,138 | 33,138 | 0 | 0 / 0 / 0 / 0 |

The dense KH change points split by the hub row that produced them: 746,236 long-array
marker rows, 270,659 `gone` rows, 11,445 rows on the path itself, and 2,659 pointer
target rows. Sampled array elements were checked against the blob.

### (a) and replay

**What Codex compared.** Codex compared the merged and flattened state under `same`.
That comparison misses three things:

- **Checkpoint events.** At a checkpoint eid, `state_at` loads the checkpoint itself,
  so the comparison is trivially true there. The change rows written AT checkpoint
  events were never checked. A mutation that drops exactly those rows survived all 30
  existing tests. It is now pinned (below).
- **Container shape.** A flattened comparison cannot see empty containers or
  list-versus-dict changes.
- **Which checkpoint replay starts from.** Random sampling does exercise replay from
  checkpoints other than genesis (16 of them on KH).

**Extra checks run by the review:**

- **Strict nested-document equality** (key sets, empties, container types) on
  330/315/310 events, including every checkpoint and checkpoint+1: 0 mismatches.
- **Folded change rows**, from genesis without checkpoints, equal the archive at every
  successful run of all three archives (`flat_bad` above).
- **Instants.** Every `t_utc_us` is nondecreasing in `ord`. All 400 sampled per archive
  equal `run_time.resolve(read_node=True)`. All real runs have quality `offset`,
  including Novera's -04:00, so the archive-hint path is exercised only by unit tests.
  No instant ties.
- **Resume.** Building Novera in 8 resumed chunks of 333 gave a ledger identical to
  the one-shot build in events, rows, checkpoints and blobs.
- **`REVERTS_TO_EARLIER` and `base_hash`**, recomputed independently, match on every
  event (KH 335, Novera 23, arbel 120 flagged; none on a zero-change event).
- **`proven`**, recomputed from `node.json` patches, matches on all 2,408/6,974/661 rows
  of patched runs.
- **`OFF_LIVE`** is absent from the code and flags.

### Defects

| # | Sev | Finding | Status |
|---|---|---|---|
| 1 | P1 | **An in-flight newest run became a permanent error event, and its changes moved to the next run.** On a live root (an archive grew during the builder's own run), a folder whose `node.json` exists before `quam_state` is saved was committed as an error. Completed locations are never revisited, so after the run saved, v 1->2 was still recorded at the following run. | Fixed: the newest discovered run without a readable pair is reported as `deferred` and not committed. Pinned by `test_newest_run_without_saved_state_is_deferred_not_committed`. |
| 2 | P1 | **Error events overwrote the node's status with `"error"`.** Of the 23 real stateless runs, 18 *finished* (9, 8 and 1 in the three archives); the other runs of the second archive say `error` (3) and `running` (2). The Calibration log would have shown all 23 as errors. | Fixed: `status` keeps `metadata.status`, and the reason stays in `error`. The existing assertion that encoded the defect was corrected. |
| 3 | P1 (pin) | **Resume correctness was unpinned.** Two real mutations survived every pin: diffing the first resumed run against `{}`, and forgetting the head hash. The third probe, which forgets the head shape, is equivalent. | Pinned: `test_resumed_build_equals_one_shot_build_row_for_row` (resume after every event, checkpoint interval 2). |
| 4 | P1 (pin) | **Change rows at checkpoint events were unverified** (see (a)). | Pinned: `test_change_rows_alone_reproduce_every_saved_state`. |
| 5 | P1, S5 | **Run folders are not immutable** (premise of DESIGN 3.1). See the measurements below the table. | Addressed in S5 ([275](275_the_ledger_keeps_itself_current.md)): every ingested run folder keeps a stat watermark, and a rewrite is re-diffed in place (`REWRITTEN`). |
| 6 | P2 | A run whose `node.json` is unreadable but whose saved pair is fine becomes an error event, so its changes move to the next run. Not seen in the three archives; it is now deferred when it is the newest run. | Fixed in S5 ([275](275_the_ledger_keeps_itself_current.md)): both builders keep the saved state, flagged `NODE_UNREADABLE`. |
| 7 | P2 | `read_pair` and `node.json` reads use a plain `open()` without `FILE_SHARE_DELETE`. The project's doctrine routes reads of externally written files through `safe_io.open_shared`. | Fixed in S5 ([275](275_the_ledger_keeps_itself_current.md)): every run file is read through `safe_io.open_shared`, pinned by `test_run_files_are_read_through_a_share_delete_handle`. |
| 8 | P3 | `REVERTS_TO_EARLIER` is byte-based. A content-equal re-serialisation of an earlier state would flag a zero-change run (none seen). | Open |
| 9 | P3 | The `state_hash` lookups for reverts and identity have no index. `HubStore()` writes `meta` on every open, so a reader takes the write lock briefly. | Fixed: `events_by_state_hash` (S4); `HubStore()` writes `meta` only when a key is missing ([275](275_the_ledger_keeps_itself_current.md) review round). |
| 10 | P3 | In `tools/mutate_hub_builder.py`, `uncertainty` and `conflicting_chip`, and also `numeric_equal` and `revert_head`, are the same edit. 36 mutations are 34 distinct ones. `str.replace` also mutates every occurrence. | Noted |

**Measurements behind defect 5.** These runs had `state.json` written more than 60 s
after the run instant, and some were rewritten after later runs had already started:

| Archive | Rewritten >60 s after the run | Of those, after later runs had started |
|---|---:|---:|
| KH | 70 | 32 |
| Novera | 127 | 2 |
| arbel | 10 | 0 |

The KH cases include:

- #1-#31: rewritten together at 18:53 on 2026-09-07; the rewrite dropped `twpa_ext`.
- #146: the deferred acceptance described above.
- #2483: rewritten on 2026-09-29, 16 days later, with identical bytes.

Consequences:

- Resumed builds never see such rewrites.
- The ledger depends on when it was built.
- A deferred acceptance is dated at the run's instant, not the time it took effect.

S5 needs per-location `(size, mtime_ns)` (or a rehash: about 6 s for KH) and a
correction-event policy.

**Environment observation, outside this branch.** The KH archive's files are hardlinked
into `D:\work\sm_qa_rigs\_shared\KH_202608_CZ` (created 2026-09-25). The #2483 rewrite on
2026-09-29 happened through one of those two names. Any rig that writes in place there
writes into `D:\work\Customer_Codes`.

### Mutations and tests

**Mutation checks:**

- Codex's 36 mutations, re-run on the fixed tree: **36/36 RED**.
- The review's own runner replaces exactly one occurrence of each target. Six of
  Codex's mutations re-run there were RED: `checkpoint_replay`, `keep_error`,
  `late_guard`, `identity_hash`, `array_replay`, `revert_fact`.
- Of the review's 12 new probes, 9 were RED. These covered adjacent-revert flagging,
  the lossy old side, the `/quam` envelope, `base_hash`, zero-change uncertainty, the
  status overwrite, the `state_at` bound, the checkpoint payload and tie order.
- The 3 resume probes stayed GREEN (defect 3). Dropping rows at checkpoint events
  (interval > 1) also stayed GREEN under all old tests (defect 4).
- The new pins are RED under every one of those mutations:

  | Pin | Mutations that turn it RED |
  |---|---|
  | In-flight | never defer; defer every error; off by one |
  | Status | status restored to `"error"` |
  | Resume | diff the first resumed run against `{}`; forget the head hash |
  | Rows-only | drop rows at checkpoint events |

**Tests (cqt).** The hub suite (`test_hub_store`, `test_hub_build`, `test_hub_rules`)
was run with `test_one_run_instant`, `test_run_instant`, `test_run_instant_callers`,
`test_clock_health`, `test_leaf_index`, `test_history`, `test_param_history_changes` and
`test_field_history`. Result: **583 passed, 1 skipped**.

On the first run, `test_one_run_instant::TestRekeyMigration::test_revert_keeps_a_label_written_after_the_rekey`
failed once. That file does not import the hub. It passed in isolation and in a full
re-run, so it is recorded as a flake.

**Sizes and timings:**

- Rebuild times: KH 87 s, Novera 23 s, arbel 80 s (1,611 runs, live).
- Peak RSS for arbel: 61.7 MiB.
- Per-event write transaction on arbel: p50 23 ms, p95 30 ms, max 403 ms.
