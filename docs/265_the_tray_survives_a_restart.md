# 265 -- The tray survives a restart

2026-10-03, A-22, branch `fix/tray-survives-restart`.

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

`core/pending_tray.py` attaches a checkpointed list to the web context's
`QuamStore.change_log`. Append, remove, pop, clear, slice replacement/deletion,
extend, insert and reorder mutations checkpoint synchronously. Atomic HTTP
edit batches hold the store lock and defer these checkpoints until all rows,
rollback and actor stamps are complete; nested batches wait for the outer
completion. `ChangeEntry`
also checkpoints metadata assignments, including the actor stamped after a
route's Modifier call. The mutating-request completion hook checkpoints the
final dirty/stash/wholesale-base flags before returning the response.
Eviction retires the context's checkpoint ownership. Requests still holding
that old context cannot write or delete the current sidecar; reopening binds
the store to the fresh context. This preserves the parked-model rejection gate.

The sibling file `instance/working_state/<key>.pending_tray.json` contains the
exact rows (old/new values, actor, group ID, source and create/delete markers),
the in-memory state/wiring pair, mutation sequence, context flags, disk-content
hash and live sync-point hash. It uses `safe_io.atomic_write_json`, the undo
journal's flushed-temp/atomic-replace primitive. An empty log drops the file.

A fresh context first loads its existing working files as before. Recovery
validates the whole checkpoint before publishing its documents or rows:
working content must equal the checkpoint's disk base or its complete staged
pair (a save interrupted after installing the pair); live content and the
working-copy sync point must still match the recorded live anchor. Recovery
writes neither the working files nor the live chip. The restored rows use
the ordinary Apply and Ctrl+Z machinery.

Recovery also validates row metadata, document shapes, context flags and the
mutation sequence. It prepares the copied documents, merged view and cached
wiring JSON before publishing anything. Replay-stash operation/value/group
tuples are decoded after JSON roundtrip so the existing sync machinery can
recognize their operations.

If a check fails, no row is restored. The checkpoint is renamed to a unique
`.unrecovered-<id>.json` file. The next tray render shows one English alert
naming the edited dot paths, live folder, working folder and retained recovery
file. Further renders do not repeat the alert. An unreadable checkpoint cannot
supply dot paths, so its notice names the file and folders instead. If the
rename is refused (for example, a locked sidecar), load still succeeds and the
notice names the original file and the retention error. Checkpoint write
failures propagate rather than acknowledging durability that was not achieved.

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

## Pins and measurements

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

## Mutation result

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

## Open items

- Recovery is conservative: any live-content drift refuses the complete
  checkpoint, even when the drift is on an unrelated field. The retained file
  is for inspection; there is no automatic conflict-resolution restore.
- A pre-upgrade session that never wrote a pending sidecar cannot reconstruct
  edits that existed only in the previous process's memory.

The requested timing flake remains excluded; correcting its wall-clock
sensitivity is outside A-22. No required A-22 work remains. No real SM server
or real chip was used. The `.task-tmp/` scratch folder was removed after
recording the final results.
