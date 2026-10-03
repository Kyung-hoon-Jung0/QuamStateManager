# 265 -- The tray survives a restart

2026-10-03, A-22, branch `fix/tray-survives-restart`. Redesigned for speed
2026-10-04 (see "Redesign"); the first cut is 3961144e.

## Measured before changing code

`test_restart_restores_rows_values_flags_and_undo` stages two edits through
`/field/edit` on a temporary chip, clears the process context cache, builds a
NEW app on the SAME instance directory, and `/load`s that chip again.
Creating another app alone is insufficient: the routes module owns a process
cache, which must be dropped to model a restart.

Before restart, RAM holds `qubits.q1.f_01`: 5000000000 -> 5100000000 and
`qubits.q1.z.joint_offset`: 0.08 -> 0.12. There are two tray entries, both
actor `human`, group ID `None`. The working-copy `state.json` still holds
5000000000 and 0.08; field edits have NOT been saved to that file. Live also
remains unchanged. `working_dirty` is false (that flag describes saved
working content), while the change log supplies the unsaved dirty count.

After restart, the working store shows 5000000000 and 0.08, the tray has zero
rows, `working_dirty` and `live_diverged` are false, and `pending_reapply` is
None. `/load` redirects successfully; the resulting page has no recovery or
loss notice. The restart pin is RED at the row equality assertion.


## Design

(Redesigned 2026-10-04 for speed. The first cut, 3961144e, is under "First
cut" below; "Redesign" has the measurements.)

### What is persisted

`core/pending_tray.py` keeps one sidecar per chip,
`instance/working_state/<key>.pending_tray.json` (format version 2), with:

- the exact rows: every `ChangeEntry` field, i.e. dot path, old/new value,
  source file, create/delete markers, group ID and actor;
- the context flags sync needs (`working_dirty`, `staged_base`,
  `pending_reapply`, `pending_reapply_orig`);
- `mutation_seq`, so group IDs minted after a restart can never collide with
  restored ones;
- the live sync-point hash;
- the identity of the working pair the rows apply on top of: `base_hash` (its
  content hash) and `applied` (how many leading rows those files already hold;
  normally 0).

The documents themselves are **not** stored. A 200-row tray is ~42 KB on any
chip. The file is written with `safe_io.atomic_write_json` (flushed, fsync'd
temp + atomic replace). An empty tray removes it.

### When it is written

- **Inside a request, nothing is written on the request thread.** Every log
  mutation (append, pop, slice, clear, ...) and every row-metadata assignment
  (the actor stamped after a route's Modifier call) only marks the chip:
  O(1), no I/O. A `/field/edit` makes three marks.
- **The mutating-request completion hook** marks every chip with pending
  rows, so the dirty/stash/wholesale-base flags set late in a request are
  carried too.
- **When the request ends** (`teardown_app_request`), its chips are handed to
  one background writer thread. It lands ONE write for everything changed in
  the window: 0.1 s after the last change (`DEBOUNCE_S`), never later than
  0.3 s after the first unwritten one (`MAX_DELAY_S`).
- **A chip a running request touched is not written until that request
  ends,** so no write carries half a request (rows before the actor stamp,
  rows a rollback is about to take out). The scope lives in the request's
  WSGI environ, so a nested `test_request_context` (the agent's apply door)
  cannot end it early. A chip already pending from earlier requests waits at
  most 2 s (`HELD_MAX_S`).
- **A write is one consistent snapshot,** taken under the working copy's
  build lock and the store lock. So a write never pairs the rows with a
  working pair that a sync pull or a stage is replacing wholesale. The
  background writer waits at most 50 ms for either lock, then backs off
  (doubling, up to 1 s).
- **The base hash is cached per stat fingerprint of the working pair:**
  - right after a chip opens, it is the hash the build already memoized
    (docs/189);
  - after a save, `doc_cache` knows the bytes SM wrote, so no parse is needed.

  A write therefore normally reads nothing back.
- **A request that empties the tray** (Apply, undo of the last row) unlinks
  the sidecar itself, synchronously, after any write in flight. An applied or
  fully undone tray can never come back.
- **Outside any request** (a background thread, a script) the write happens
  at the mutation, as before. `Modifier.batch_set` defers its hooks to one
  write per batch (the autofit writer's path).
- **A clean exit (`atexit`) lands pending writes,** and so does re-opening a
  chip in the same process.

**Durability window:** a crash (kill, power loss) loses at most the edits
staged in the last 0.3 s, plus the one write in flight (~10 ms). Edits older
than that are on disk.

### Restore

A fresh context first loads its working files as before. Recovery then
validates the whole checkpoint before publishing anything:

- the version and live folder;
- the row metadata, flags, stash and `mutation_seq`;
- `base_hash` (a string) and `applied` (an int in range);
- the working-copy sync point, and that the live content still matches it.

Then:

- **Working files == the recorded base** (the normal case). The rows are
  re-applied through the Modifier to a COPY of the documents, exactly as first
  applied: `set_value(coerce=False, enforce=False)`, `create_subtree(...,
  enforce=False)`, `delete_subtree`. Every row must land where it landed
  before:
  - its path must navigate;
  - its source file must be the same;
  - a create must find the key absent;
  - the value it replaces must be the one the row recorded.

  The exception is a row inside a container an earlier row wrote: that
  object was edited in place afterwards, so its recorded content is the
  later one. Such rows are covered by the base hash.
- **Working files == the base plus the rows.** This is a save that installed
  the pair but never cleared the log. It is proven both ways by content hash:
  - taking the rows back out must leave exactly the base;
  - putting them back must give exactly the files.

  The files then become the store as they are, and `applied = n` is recorded
  for later writes. A later restart re-applies only the rows the files do
  not hold. If a held row then leaves the log while the files stay (an undo
  of a restored row), no (base, rows) pair describes the state. The
  checkpoint is written as unverifiable, and a restart refuses it (with the
  notice) instead of guessing.
- **Anything else is refused.**

Only after every row has landed is the copy published:

- documents, merged view (`merge_state_wiring`) and cached wiring JSON;
- rows, flags and `mutation_seq`;
- a `store_revs` reload event, so structure caches rebuild;
- a fresh search index if one had been built.

Recovery writes neither the working files nor the live chip. The restored
rows use the ordinary Apply and Ctrl+Z machinery. Replay-stash
operation/value/group tuples are decoded after the JSON round trip.

### When recovery is refused

If any check fails, no row is restored:

- The checkpoint is renamed to a unique `.unrecovered-<id>.json` file.
- The next tray render shows one English alert naming the edited dot paths,
  the live folder, the working folder and the retained recovery file.
  Further renders do not repeat it.
- An unreadable checkpoint cannot supply dot paths, so its notice names the
  file and folders instead.
- If the rename is refused (for example, a locked sidecar), load still
  succeeds, and the notice names the original file and the retention error.

A background write that fails is retried (backoff up to 1 s). The first
failure of a streak is announced once in the tray: "Staged edits are not
being saved for a restart: ...". An explicit `checkpoint(ctx)` / `flush_all()`
raises what the write raises.

Eviction retires the context's checkpoint ownership. Requests still holding
that old context cannot write or delete the current sidecar. Reopening binds
the store to the fresh context, which preserves the parked-model rejection
gate. Re-attaching the same chip in-process lands the old context's pending
write first, and then retires it.

## Invariants

- **docs/65:** wholesale loads keep an empty log; recovery invents no entries
  for loaded content. Mixed wholesale+edited state preserves `staged_base`,
  the dirty flag and stash, so sync keeps the whole staged base.
- **docs/87:** the existing user-facing reconcile stays `sync_if_clean=False`.
  A mismatch keeps the loaded working files and announces failed recovery;
  it does not silently adopt live or a subset of the checkpoint.
- **docs/255:** preflight refusal leaves the checkpointed log intact. A late
  refusal's existing slice insertion restores the checkpoint along with the
  rows. Successful save/apply empties the log and drops its pending sidecar;
  the saved undo journal remains the cross-save history.
- **docs/255, after the redesign:** a refused press puts back the SAME row
  objects and the working bytes with their mtimes, so the next write's base
  hash (cached per stat fingerprint) and held-row count describe it exactly.
- **No partial request on disk:** a chip a running request touched is written
  only after that request ends (bounded at 2 s for a chip already pending).

## Redesign (2026-10-04): the first cut cost 0.9 s per edit

The first cut (3961144e) was correct, but it did all of its work
synchronously, under the store lock, on the request thread. Every log or row
mutation (including the actor assignment on a `ChangeEntry`) ran a full
checkpoint, which did three things:

- read and parsed the working pair from disk;
- hashed it;
- wrote the full `store.state` + `store.wiring` with fsync.

A single `/field/edit` fired three of them: the append, the route's actor
stamp, and the request-completion flags. Undoing a group popped its rows one
by one, so it fired one per row.

### Method

`bench.py` lives in the session scratchpad. It drives a Flask test client
in-process against a COPY of each chip (no server, no real chip):

- **rC:** the 21-qubit agent rig chip, a 0.95 MB `state.json`.
- **big30x:** the existing 19.4 MB synthetic chip, copied from
  `sm_qa_rigs/_shared/bigstate`.

Each run does:

- 5 warm-up edits;
- 50 timed single-cell `/field/edit`s on distinct float leaves;
- 20 timed single `/undo`s;
- 3 rounds of a 200-row `/field/edit-batch` paste, each followed by its
  one-group `/undo`.

What it counts:

- **writes:** each call to `safe_io._replace_into_place` on the sidecar path,
  with the size of the temp file that lands;
- **hook calls:** each call to `pending_tray._mark` (or to `checkpoint` for the
  first cut).

Two pacing modes:

- **back-to-back:** requests with no gap;
- **paced N ms:** a sleep of N ms after each edit and undo, so the background
  writer writes between requests.

The machine is shared, and base latency alone moves 1-3 ms between sessions.
So every number below is compared with a base run interleaved in the same
session.

### Results

First cut, back-to-back (session 1):

| | base 0b46c043 | first cut 3961144e |
| --- | --- | --- |
| edit p50 / p95, rC | 1.88 / 2.39 ms | 69.9 / 93.0 ms |
| edit p50 / p95, big30x | 2.04 / 2.64 ms | 898 / 1012 ms |
| checkpoints / writes per edit | 0 / 0 | 3 / 3, all on the request thread |
| bytes written per edit | 0 | 3.15 MB (rC), 62.8 MB (big30x) |
| single `/undo` p50, rC / big30x | 3.9 / 3.1 ms | 51.8 / 578 ms |
| 200-row paste, rC | 11-15 ms | 66-69 ms (2 writes, 2.2 MB) |
| 200-row paste, big30x | 12-14 ms | 568-689 ms (2 writes, 42 MB) |
| undo of that 200-row group, rC | 11-15 ms | 4,855-5,263 ms |
| undo of that 200-row group, big30x | 12-15 ms | **67,557-76,442 ms** |

Final code, i.e. everything in this commit (sessions 3-4):

| | base 0b46c043 | final |
| --- | --- | --- |
| edit p50 / p95, rC, back-to-back | 2.30 / 2.51 ms | 2.31 / 3.59 ms |
| edit p50 / p95, big30x, back-to-back (two runs) | 2.48 / 3.70; 2.17 / 3.56 ms | 2.36 / 4.12; 1.92 / 2.29 ms |
| edit p50 / p95, rC, paced 250 / 100 ms | 4.67 / 5.56; 4.31 / 4.89 | 4.63 / 5.60; 4.64 / 5.91 |
| edit p50 / p95, big30x, paced 250 / 100 ms | 4.51 / 5.23; 3.45 / 5.13 | 3.83 / 5.35; 4.57 / 5.84 |
| hook calls per edit | 0 | 3 marks, O(1), no I/O |
| writes per edit | 0 | none on the request thread; background: 0.02 back-to-back (1 per 50 edits), 0.32-0.34 paced 100 ms, 1.0 paced 250 ms |
| bytes written per edit | 0 | 213 B back-to-back; 5.7 KB paced 250 ms (rC and big30x alike) |
| single `/undo` p50, rC / big30x | 3.04 / 3.57 ms | 3.57 / 3.51 ms |
| 200-row paste, rC / big30x | 13.5-14.4 / 14.3-18.0 ms | 14.4-15.8 / 15.2-15.8 ms (1 background write, 42 KB) |
| undo of that 200-row group, rC / big30x | 13.1-14.1 / 14.7-15.1 ms | 15.4-16.0 / 15.7-16.1 ms |

The paced modes raise base itself from ~2 ms to ~4 ms per edit (an idle CPU
between requests). Read each row against its own base. Across every mode the
final per-edit p50 is within 1.1 ms of base, and often below it. The first
cut added 68 ms (rC) and 896 ms (big30x).

One small atomic write with fsync measured 9-11 ms p50 on this machine
(11-41 KB, 55-200 rows). That is why the write left the request thread rather
than only getting leaner: even an ideal synchronous checkpoint would have
used the whole 10 ms budget.

### What changed (vs 3961144e)

- **Format version 2 stores rows, not documents.** It adds `base_hash` +
  `applied` and drops `state` / `wiring` and the disk-content hash. See
  "Design".
- **Restore re-applies the rows through the Modifier** to a copy of the
  verified working documents, and publishes only if every row landed. The
  first cut published a stored snapshot instead.
- **The interrupted-save fallback is proven both ways** (rows out == base,
  rows back == files). The first version of this redesign checked only "rows
  out == base". The unchanged `[working]` refusal pin caught it: a revert
  overwrites whatever value the file holds, so a changed working file passed.
- **Marks instead of writes on the request thread.** The request scope is
  opened by `before_app_request` and closed by `teardown_app_request`. One
  background writer is debounced 0.1 s and bounded at 0.3 s. An emptied tray
  is unlinked synchronously at the end of its own request.
- **Snapshots run under the build lock + the store lock.**
- **The base hash is cached per working-pair stat fingerprint**, seeded from
  the open-time memo.
- **`Modifier.batch_set` defers its checkpoints** to one per batch.
- **Restore records a `store_revs` reload and resets a built search index.**
- **The request-completion hook only marks** (`note_flags`); it no longer
  writes.

### Pins adapted, and why

The first cut's pins asserted two things this redesign changes by request:
the full-state storage format, and a write that completes before the request
returns. Every other assertion is unchanged.

**Format (the sidecar no longer stores the documents):**

- `test_partial_checkpoint_never_publishes`:
  - `state-shape` and `wiring-shape` mutated fields that no longer exist.
    They are replaced by `base-shape` (`base_hash: null`), `applied-range`,
    `applied-type` and `base-mismatch`.
  - `version` now uses `VERSION + 1`, since 2 is the current format.
  - Two new row cases: `row-old-value` (a row whose old value is not in the
    base) and `row-source-moved`.
- `test_save_interruption_recovers_complete_snapshot_without_writing` read
  `doc["state"]` / `doc["wiring"]` to build the installed pair. It now uses the
  staged pair the checkpointed store held. Same assertions.

**Timing (no write on the request thread):**

- `tests/test_tray_restart.py::_staged` calls `pending_tray.flush_all()` after
  its two edits: wait for the background write. Without this, the `[corrupt]`
  case's corruption was overwritten by the still-pending write.
- `test_sidecar_crash_before_replace_keeps_last_complete_tray`: the request
  now succeeds. The crash is raised by the write after it, through
  `flush_all()`. The crashed process is then retired, as on eviction, so
  nothing it still holds can land later. The prior sidecar is still
  byte-identical, and a restart still recovers exactly its two rows.
- `test_http_batch_persists_only_complete_rows_and_actor` asserted "one batch
  commit, then the request-completion flags checkpoint" (2 writes). It now
  asserts that the request thread wrote nothing, then exactly ONE write after
  the request, with the same complete rows and actor.

The 11 list-mutation pins are unchanged, and so are the direct-metadata,
request-flags, batch, nested-batch, retired-ownership and replay-stash pins.
Those mutations happen outside a request, where the write is still immediate.

### New pins

`tests/test_tray_restart_writer.py`, 10 cases:

- The sidecar carries rows, not documents: its exact key set, a size under
  4 KB, and a base hash equal to the working pair's.
- Five requests inside the window: nothing written, then ONE write with all
  rows.
- Without any flush, the background writer lands a request within the window.
- Undo of a 50-row group is one write, never 50. The request that empties the
  tray removes the file itself.
- A restart replays created, deleted and nested rows exactly: a create, then
  an edit inside it; a container written whole, then edited inside; a delete;
  a grouped batch; an actor stamp. The state, merged view and rows are
  identical, and undo-all returns to the base.
- An interrupted save, then a new edit: the second restart restores all three
  rows (the `applied` count).
- Undoing a row the files already hold refuses the next restore instead of
  guessing.
- A write never snapshots while a wholesale replacement holds the build lock;
  it lands right after.
- A failing background write is announced once, and not again on its retries.
- An out-of-request `batch_set` of 20 rows is one write.

### Regression

The environment and flags were the same as the first cut's: `cqt`,
`PYTHONUTF8=1`, `-p no:cacheprovider --timeout=900 --timeout-method=thread`,
one math thread each.

**The broad run.** A case-insensitive grep over `tests/test*.py` selected
**251 files**:
`working_copy|change_log|pending[ _-]tray|undo_journal|state_roundtrip|sync|auto_apply|refused_apply|loader|tray`.
That is the first cut's 228 files, widened by `tray`. They ran in 4
invocations, with only the known jsdom flake deselected:
`tests/test_autosync_merge.py::TestTheClientPressesTheDoorItIsGiven::test_the_shipped_client_behaves`.

- Result: **7,726 passed, 139 skipped, 1 deselected, 1 failed.**
- The one failure was a new pin of this redesign. It read the sidecar with a
  plain `read_text` while the background writer was replacing it, and got a
  `PermissionError` on Windows. It now reads as SM reads (`safe_io.read_json`:
  share-delete + retry). The same race was the one non-crash GREEN failure of
  the first mutation pass.

**After the `batch_set` deferral.** The 72 files that mention `batch_set`,
`Modifier(` or `autofit` were re-run in 3 invocations: **2,640 passed,
76 skipped, 0 failed.** All A-22 pins (69) pass.

### Mutation result (this redesign)

The sweep runs on an isolated copy of `quam_state_manager` + the A-22 test
files in the session scratchpad, so it never touches the worktree. A subprocess
probe confirms that the test processes import from that copy.

- Each mutation must make its pins fail (pytest exit 1). It is then
  restored, and the same pins must pass (exit 0).
- Collection errors, crashes and timeouts are not credited.
- **24 mutations cover all 21 new or changed pins** (the `_staged` change is
  covered through the `[corrupt]` case). **24/24 confirmed RED -> restored
  GREEN.**

| Pin | Mutation(s) |
| --- | --- |
| `[corrupt]` via `_staged` | `flush_all` a no-op |
| crash before replace | a failed write swallowed; retirement ignored |
| HTTP batch (+ emptied tray) | request scope ignored, i.e. writes on the request thread |
| partial `version` / `applied-range` / `applied-type` | each check removed or loosened |
| partial `base-shape` (+ held-row refusal) | a null base treated as "the files as they are" |
| partial `base-mismatch` (+ the unchanged `[working]` pin) | interrupted-save check always passes |
| partial `row-old-value` / `row-source-moved` | the replay's old-value / source-file check removed |
| save interruption | interrupted-save recovery disabled |
| rows, not documents | the documents written back into the sidecar |
| coalescing | the request end writes synchronously |
| background writer lands it | the writer never finds anything due |
| emptied tray | the end-of-request drop removed |
| replay exactness | no in-place exception; deletes not replayed |
| interrupted save + new edit | the held-rows count never recorded |
| held row undone | a shortened held prefix still counted |
| build lock | the snapshot ignores the build lock |
| failure announced once | never announced; announced on every retry |
| batch outside a request | `batch_set` without the deferral |

A first pass ran beside the 4-way regression on the shared machine. Three
runs there aborted with 0xC0000409, and one shell launch returned 127 (the
process could not even start). Those were not credited. In isolation those pins
did not abort: 6/6 passed for M06's pin, and 7/8 for M20/M22's (the eighth
hit the read race fixed above). The full sweep, re-run afterwards, credited
24/24.
The original 61 mutations are recorded under "First cut".

### Real browser (headless Chrome)

**Setup:**

- SM served from this worktree under waitress on 127.0.0.1:5127.
- The instance dir, HOME/USERPROFILE and the chip are a COPY of the rC rig
  chip in the session scratchpad. The chip network stays 127.0.0.1:1.
- Driven over CDP with `tests/browser/journeys/cdp.cjs` and real mouse and
  keyboard events.
- CDP ran on **9448**: 9447 is held by LogiPluginService on this machine, and
  was left alone.

**What happened:**

1. Typed three edits into Live State Edit: `qA1.T1` 3.61e-05, `qA2.T1`
   4.27e-05, `qA3.T2echo` 2.75e-05. The tray read "3 unapplied edits"; the
   live file was untouched. The sidecar was 939 bytes: three rows, version 2,
   no documents.
2. Hard-killed the server process (`Stop-Process -Force`: no atexit), and
   confirmed the port refused connections. Started a new server.
3. In a new tab, loaded the chip:
   - the tray showed 3 edits;
   - `/api/agent/tray` returned the identical rows (path, old, new, actor,
     group);
   - the cells showed the three typed values.
4. Pressed Ctrl+Z: the tray dropped to 2, and qA3.T2echo returned to
   4.0306e-05 (toast "Undone: qubits.qA3.T2echo"). Retyped it.
5. Pressed Apply in the tray: "re-applied 3 edits, and applied them to the
   live chip". The live `state.json` carried all three values, the tray read
   "In sync", and the sidecar was gone.
6. Pressed Ctrl+Z again: "Undone → live: qubits.qA3.T2echo → 4.030632e-05".
   The live file and the cell were back to the original value.

Console errors: **0** in both phases. The five screenshots were read; the red
banner in them is the rig chip's own pre-existing qB3 diagnostic. Only the
server and Chrome this rig started were killed (both matched by command
line). The chip copy, instance dir, home and Chrome profile were deleted.

## First cut (3961144e): pins, regression and mutations

Recorded as it was for the first cut. Its numbers describe 3961144e, not
the redesign; the pins it lists are still in the suite (seven adapted, see
"Pins adapted, and why").

### Pins and measurements

`tests/test_tray_restart.py`: **7 cases**. The original pins are unchanged.
`tests/test_tray_restart_recovery.py`: **48 additional cases**.
All **55/55 new cases passed**, including in the final regression; none skipped.

- Route edits -> NEW app -> same chip: identical rows and values, flags,
  live bytes unchanged; Ctrl+Z walks both entries and drops the sidecar.
- Three refused-recovery cases: changed live, changed working files, corrupt
  sidecar. All-or-nothing; one alert; paths named; recovery file retained.
- Crash after writing the new temp but before atomic replacement: the prior
  sidecar remains byte-identical; a restart recovers its complete rows.
- Apply after restart: the exact restored values reach live, other document
  content stays intact, and the saved journal names exactly those rows.
- Grouped agent edits over a wholesale base: actor/group metadata and flags
  survive; one Ctrl+Z reverts the group; sync still applies the whole base.
- Twenty-five malformed or partial checkpoint cases reject the entire restore:
  row/document shapes, identity/version, dirty/stash flags, row metadata,
  mutation sequence and live anchor.
- Retention rename failure and snapshot preparation failure leave the loaded
  store usable without publishing staged values or rows.
- A complete pair installed before a save interruption is recoverable; spies
  refuse any recovery write to chip documents.
- Changed working-copy sync metadata refuses recovery. Wholesale content stays
  logless. All eleven supported list mutation operations checkpoint complete
  rows; direct actor assignments and flags set after a request's log mutation
  are durable. Replay stash operation/value/group tags and originals survive.
- Batch completion, nested batching, and the HTTP batch pin prove that no
  intermediate rows or unstamped actor metadata reach the checkpoint.
- A retired context cannot change or delete a fresh context's checkpoint.

The initial two-edit pin was RED before production changes. A preliminary
working-copy/state-roundtrip/undo-journal slice passed **94, skipped 3**.
The final seven cases plus unchanged refused-Apply file passed **34**.
The final A-22 pins plus the unchanged 2,000-edit batch throughput test passed
**55** (54 A-22 cases and one existing case). During regression, that existing
test caught a real performance problem in the initial implementation:
per-row checkpointing took **130.97 seconds** for 2,000 edits. Deferring disk
writes and row-hook binding to batch completion passes its unchanged
**5-second ceiling**. No throughput threshold or test was changed.
The unchanged parked-chip file then caught stale checkpoint ownership:
`test_a_late_edit_on_a_parked_store_is_never_served` rejects a mutated parked
model, but its late edit had written a sidecar that resurrected that model's
values. Retiring the evicted context's checkpoint ownership preserves the
existing behavior. All A-22 pins, that entire existing file, and the throughput
pin passed **78** after the fix (55 A-22 cases and 23 existing cases).

The previous regression stopped at **92 passed, 1 failed**, on:

`tests/test_autosync_merge.py::TestTheClientPressesTheDoorItIsGiven::test_the_shipped_client_behaves`

The coordinator reproduced the same wall-clock timing failure on the unchanged
base ("pressing 2.5s late" and "a timer was still alive after the bound") on
this heavily loaded machine. The resumed regression deselects **only that
exact test ID**, as instructed. Neither the test nor its JavaScript was edited.

The final case-insensitive grep selects **228 Python test files**, using
`working_copy|change_log|pending[ _-]tray|undo_journal|state_roundtrip|sync|auto_apply|refused_apply|loader`
under `tests/` with `-g 'test*.py'`. This includes all the earlier selected
files and widens coverage to loader mentions and alternate tray spellings.
The run uses `D:/miniconda3/envs/cqt/python.exe`, `PYTHONUTF8=1`,
`pytest -p no:cacheprovider --timeout=900`, temporary directories inside this
worktree, and the exact `--deselect` above. Native math thread pools are limited to one thread with
`OMP_NUM_THREADS=MKL_NUM_THREADS=OPENBLAS_NUM_THREADS=NUMEXPR_NUM_THREADS=1`
to limit this run's load on the shared machine.

Final regression, completed **2026-10-04**: **7,200 passed, 115 skipped,
1 deselected, 0 failures, 0 errors**, across all **228 selected files** in
**2,685.56 seconds (44:45)**. The 115 skips come from existing test conditions,
primarily unavailable large/private chip fixtures and corpora. No additional
test was deselected, edited or weakened. The XML report contains 7,315 test
cases and confirms that all 55 A-22 cases passed.

### Mutation result

The sweep uses an isolated source copy inside this worktree. Each mutation is
run against its own pin, must fail its pytest test (RED), is restored
in `finally`, and must pass the same pin (GREEN). The production copy and
existing tests are never mutated. There are **61 mutations covering all 55
new test cases and the unchanged parked-model guard**, including separate
actor/group and recovery/cleanup breaks inside the shared route pins.
**61/61 confirmed RED -> restored GREEN.** Every new case was exercised.
Incomplete subprocess exits and the harness's collection error were not
credited; those invocations were corrected or rerun to obtain actual pytest
RED and GREEN results. Each invocation uses a fresh temporary directory.

| Guards exercised | Mutations |
| --- | ---: |
| Original route recovery, mismatch, alert, atomic-write, Apply and grouped-base pins | 10 |
| Malformed/partial checkpoint rejection pins | 25 |
| Retention/preparation errors, save interruption, sync metadata, logless wholesale content | 5 |
| Supported list mutation hooks | 11 |
| Direct row metadata and request-completion flags | 2 |
| Replay-stash tuple decoding | 1 |
| Batch completion, nesting, and complete HTTP batch metadata | 3 |
| Retired checkpoint ownership and eviction's existing parked-model guard | 2 |
| Explicit live-write and partial-mismatch publication violations | 2 |

The requested timing flake remains excluded; correcting its wall-clock
sensitivity is outside A-22. No required A-22 work remained at the first cut.
No real SM server or real chip was used for it. The `.task-tmp/` scratch folder was removed after
recording the final results.

## Open items

- **Durability is bounded, not immediate.** A crash loses the edits staged in
  the last ~0.3 s, plus one write in flight. A clean exit and an in-process
  re-open land them first.
- **Out-of-request mutations other than `batch_set` still write once per
  mutation**, synchronously and under the store lock (~10 ms each with
  fsync). Today those are the autofit writer's per-row `set_value` reverts:
  rare, and on a background thread, so they block a request only for that
  long. Wrapping those loops in `pending_tray.batch(store)` would make them
  one write.
- **Restore reads the sidecar with a single attempt** (the first cut's
  choice). An antivirus scanner holding the file at the moment of a restart
  would refuse the restore, rename the file and show the notice, rather than
  retry.
- **Two SM processes with the same chip open** both write its one sidecar;
  the last write wins (unchanged from the first cut).
- **Undoing a row that an interrupted-save recovery found already in the
  files** leaves the tray unverifiable until the next save. A crash in that
  state refuses it with the notice; the values themselves are in the working
  files.
- **Recovery is conservative.** Any live-content drift refuses the whole
  checkpoint, even when the drift is on an unrelated field. The retained file
  is for inspection; there is no automatic conflict-resolution restore.
- **A first-cut (version 1) sidecar is refused** with the notice. That
  version never shipped.
- **Edits that existed only in memory before this build cannot be
  reconstructed.** A pre-upgrade session never wrote a sidecar.
- **The jsdom timing flake stays deselected**; correcting its wall-clock
  sensitivity is outside A-22.
