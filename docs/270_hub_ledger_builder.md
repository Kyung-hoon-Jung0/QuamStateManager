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
locations, errors, raw-hash repeats, event/row totals, archive offset, and elapsed
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
   Use the run identity to admit alternate locations. Otherwise keep an event.
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

All discovered runs became events, including incomplete pairs. The synthetic
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
