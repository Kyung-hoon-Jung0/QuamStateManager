# 271: Every write SM makes to a live chip is recorded

S4 of the state-tracking hub, on `feat/hub-record` (base `219da644`).
Design source: `D:\work\study\2026-10-03_state-tracking-hub\DESIGN.md` §3.4 and §3.7 S4,
read on 2026-10-04. The S2 rules ([269](269_hub_rules.md)) and the S3 ledger
([270](270_hub_ledger_builder.md)) are used unchanged except where noted below.

Binding user decisions applied here:

- Changes SM itself makes are logged exactly: who, when, old -> new.
- Changes made outside SM are not tracked separately. They land in the next run's diff.
- No fit-vs-human split. Copied-state runs are not flagged.
- Arrays over 16 scalars are one value, and `1 == 1.0` (the S2 rules).

## What is recorded, and where

Two layers, as DESIGN 3.1 draws them.

**`history/<chip>/events.jsonl` is the primary fact.** It gets one JSON line per SM
write. The line is appended and fsync'd *before* the live files are written, so it is
write-ahead. Fields:

| Field | Meaning |
|---|---|
| `id` | 16 hex characters, made before the write. The undo journal units the write commits carry it as `meta.hub`. |
| `kind` | `sm_apply`, `agent`, `autofit`, `restore`, `undo`, `redo` |
| `t_utc_us`, `t` | SM's own clock when the line was written: UTC microseconds, and the local ISO time with its offset |
| `actor` | Who pressed. Comes from the existing actor plumbing (docs/252): `human:<name>` from `X-SM-Actor` or the `sm_actor` cookie, otherwise `human`; `by_<agent>` from `X-SM-Agent`; `autofit`; `cli`. |
| `src` | Which door (see the table below) |
| `plan_id` | `X-SM-Plan` for agent and approval presses; `autofit:<plan>` for the autofit writer |
| `run_uid` | The dataset run whose state was applied |
| `ref` | The staged snapshot, a restore's snapshot and backup, or a run id |
| `undoes`, `units` | See *Undo and redo* |
| `base_hash`, `post_hash` | `working_copy.content_hash` of the chip before and after. This is the hash SM's sync machinery already keeps as `synced_live_hash`. |
| `live`, `pid` | The live folder written and the writing process |
| `n`, `entries` | Every change, uncapped: `{path, old, new}` plus `created` / `deleted`, `by` (who staged it) and `file`. When the serialized entries are over 256 KiB, `entries` is null and `entries_blob` names `events-<sha1>.json.gz`, a file beside the journal that is fsync'd before the line. It is a file, not a directory, because history scans every sub-directory of a chip dir as a snapshot. |

A write that fails after its line gets a second line, `{"failed": <id>, "error": ...}`
(see *Failure modes*).

**`history/<chip>/ledger.sqlite` is the projection.** It is the S3 store. One projector
thread puts each line into it off the request thread. A landed line becomes:

- an `events` row: `t_quality=sm_clock`, `status=landed`, the S2 `state_hash` of the
  exact written bytes, and the flags;
- its S2 change rows;
- an `sm_events` row: the journal id, outcome, hashes, run, units, undo links, entries
  (gz) and live folder;
- when needed, the post-state pair as a blob (`sm_anchors`).

The projector tails the journal from `meta.journal_offset`. A ledger that is behind
(another window's lines, or a crash between a line and its projection) or deleted is
rebuilt from the journal alone. Opening a chip catches up (`_hub_catch_up` in
`_activate_quam`), so no page visit is needed.

### Entries and rows

The **entries** are SM's own, so they are exact:

- From the change log the save wrote. This is the new `Saver.last_cleared`: the
  entries the save actually cleared, under the store lock, so an edit that lands during
  the save never enters it.
- From the tree difference between the chip read right before the write and what is
  written (`_wholesale_unit(..., cap=False)`). This applies whenever the working copy
  carries content the log does not name: a staged version or run state, edits that
  were saved but not applied, a forced push (Keep mine) over values the log never saw,
  or an empty log. It is computed only once the write is certain, never for a refused
  press. A write whose content equals what the chip already holds is not an event, and
  its diff is never computed.

The **rows** are S2 holder rows. `hub_entries.rows_for(entries, written document)`
lifts each SM path to its S2 root: a list on the way is the root, because a long scalar
list is one S2 holder. It reverts the entries on a copy of just those roots and diffs
the two sides with the S2 rules. The cost follows the edit, not the chip.

The "written document" the projector uses is normally only those roots. The door takes
them from its store under the store lock at commit time (`hub_entries.post_fragments`,
via `hub.store_fragments`), and takes back out any edit that is in the store but not in
the bytes, because it landed after the save. A full parse of the chip is the projector's
fallback, used for catch-up and the CLI.

[derived] Checked by brute force. On 400 random documents with random SM edits (made by
the test's own plain navigation, not by `hub_entries`), both
`rows_for(entries, post)` and `rows_for(entries, post_fragments(entries, post))` equal
`rules.diff(flatten(before), flatten(after))` over the whole documents
(`test_rows_for_equals_the_full_s2_diff_on_random_edits`).

### Replay (`state_at`)

An SM event is an *anchor*, meaning its post-state pair blob (gz level 1) is kept, when
any of these hold:

- its base is not the post-state of the event before it;
- its entries are too large to keep;
- it is the 50th SM event since the last anchor.

Otherwise `state_at` replays it from its predecessor by re-applying SM's own entries
(`hub_entries.apply_entries`). That is exact because its base *is* the predecessor.
For run events, replay starts from the newest checkpoint or anchor and crosses SM
events through their exact S2 rows.

A landed line whose bytes the projector no longer has gets the flag `DERIVED` (32):
its post-state is the predecessor plus its entries. This happens when catch-up runs
after a restart, or when the RAM bound of 16 kept writes is exceeded.

## The doors

Found by grep (`apply_to_live(`, `saver.save(`, `write_state_wiring*`, literal
`state.json` writes), not from memory. Every live write goes through
`working_copy.apply_to_live(record=...)` or, for the CLI, `hub.record_direct_save`.

| Door | Kind / src | Actor source | Entries | Pinned |
|---|---|---|---|---|
| Live State Edit Apply (`/state/apply-to-live`) | `sm_apply` / `apply` | `_request_actor()`: `X-SM-Actor`, the `sm_actor` cookie, else `human` | the log the save wrote | `test_apply_records_who_when_old_new`, `test_the_applied_units_name_the_event` |
| Keep mine (`force=1&expect_live_hash`) | `sm_apply` / `keep_mine`; base = the hash the confirm named | same | tree diff, so the outside values it replaced are named | `test_keep_mine_records_the_outside_value_it_replaced` |
| Auto-Sync push flush | `sm_apply` / `auto_apply` | same | log | `test_auto_apply_session_flush` |
| Agent apply (MCP `apply_to_live`) | `agent` | `X-SM-Agent` -> `by_<agent>`, `X-SM-Plan` | log (`by` = the agent) | `test_agent_apply_is_an_agent_event_with_its_plan` |
| Approval (`_stage_writes`, presser human) | `agent` | `X-SM-Actor` (the person), `X-SM-Plan` | log (`by` = the agent) | `test_approval_names_the_person_and_the_agent` |
| Tray Apply / Pull & apply / Auto-Sync merge (`/state/sync?mode=apply`) | `sm_apply` / `pull_apply` | same | log (or tree diff when staged/saved) | `test_pull_and_apply` |
| State History stage -> Apply; Revert last apply | `sm_apply` / `apply_staged` or `revert_last_apply`, `ref.snapshot` | same | tree diff | `test_staged_version_then_apply_records_the_wholesale_difference` |
| Restore to live | `restore` / `restore_live`, `ref.snapshot` + `ref.backup` | same | tree diff against the snapshot being written | `test_restore_live` |
| Dataset Apply to chip (and a later Apply of a staged run state) | `sm_apply` / `dataset_apply`, `run_uid` | same | tree diff | `test_dataset_apply_to_chip_carries_the_run` |
| Ctrl+Z writes live | `undo` / `ctrl_z`, `undoes` | same | the staged inverse | `test_ctrl_z_and_ctrl_shift_z_on_the_chip` |
| Ctrl+Shift+Z writes live | `redo` / `ctrl_shift_z`, `undoes` = the undo | same | the staged forward | same |
| Applied-log ✕ (staged `alr:` inverse, then any apply) | the apply's kind, `undoes` from the `alr:` group | same | log | `test_the_applied_log_revert_names_what_it_takes_back` |
| /save then a later Apply | `sm_apply` / `apply`; the saved units get `meta.hub` | same | tree diff | `test_saved_then_applied_records_the_saved_edits` |
| Autofit `RealWriter` (apply / revert / restore, incl. the pull + re-stage retry) | `autofit` / `autofit_apply:<label>`, `autofit_revert:<label>`, `autofit_restore:<label>`; `plan_id=autofit:<plan>` | `autofit`; the writer's own entries `by=autofit` | `saver.last_cleared` | `test_autofit_writer` |
| CLI `set --save` | `sm_apply` / `cli_set` | `cli` | the log, then read back | `test_cli_set_save` |

**Not SM writes, so nothing is recorded:**

| Door | Why | Pinned |
|---|---|---|
| Take live (`/state/sync?mode=discard`) | It writes SM's working copy from the chip. The chip does not change. | `test_take_live_records_nothing` |
| Auto-Sync pull, reconcile auto-adopt | Same: they adopt an outside change, which per the user lands in the next run's diff | `test_reconcile_adopt_and_auto_sync_pull_record_nothing` |
| A refused apply (preflight, or found after the save) | docs/255. The line is committed only after `apply_to_live`'s last gate. | `test_a_refused_apply_records_nothing`, `test_a_refusal_found_after_the_save_records_nothing` |
| An apply of content the chip already holds | The docs/116 adopt, or base == post: the bytes are rewritten and the chip does not change | `test_a_no_op_apply_records_nothing`, `test_rewriting_the_same_content_is_not_an_event_and_costs_no_diff` |
| `/save`, stage, the tray restart file, undo-mine, take-back | These write the working copy only. The press that later lands them records them. | classification table |
| The Experiment Runner's own node save (`run_experiment._persist_node_state`) | It is a run. A human queue item runs against the live chip and saves it as every qualibrate node does, and its run folder holds that state. S5 ingests runs as `run` events. | — |
| The autofit simulator's synthetic chip | Not a lab chip (`instance/autofit/sim`). Its `ChipHandle` names `hub_dir=None` explicitly. | `test_every_chip_handle_names_its_ledger` |

`external_observed` was optional ("only if trivial"). It is not built. Recording it
would need the previous live document at every Take live or pull, plus a diff of the
whole chip on the request thread.

## Failure modes: write-ahead, marked failed

| Case | What happens | Pin |
|---|---|---|
| The journal cannot be written (disk, permission, not a file) | `hub.RecordError` (an `OSError`) before the live write: **nothing is written**. The door's existing "the write failed, your edits are kept" branch applies. | `test_an_unwritable_journal_means_nothing_is_written` |
| The chip identity cannot be resolved | Fails closed the same way | `test_a_write_with_no_chip_to_file_it_under_is_not_made` |
| The live write raises after the line | A failure line `{"failed": id}`, fsync'd before the error reaches the door. The ledger gets a `failed` event with an `error`, no rows, and `state_at` raising. | `test_a_write_that_fails_after_its_line_is_marked_failed`, `test_a_failed_live_write_is_marked_and_claims_no_rows` |
| The write cannot be verified (read-back hash differs, or mtimes unreadable) | Counted as failed, same as above. A write nobody can vouch for is never claimed. | `test_an_unverified_write_is_marked_failed` |
| Even the failure line cannot be written | The event's own line is truncated back out, which is only possible while it is still the last line. Otherwise the loss is logged critically, and the in-process projector, which knows the outcome, still records it as failed. | `test_unmarkable_failure_rolls_the_line_back_out` |
| SM stops between the line and the outcome | At catch-up a line with no failure mark, whose writer is gone, is `landed` only on evidence: a later line was taken against its `post_hash`, or the chip holds it now. Otherwise it is `unconfirmed`: an event with an error and no rows. | `test_lines_nobody_can_vouch_for_are_decided_by_evidence` |
| Another live SM window's line | Never guessed. It waits for that window, which projects it, or for its failure line. Retried every 2 s for at most a minute. | `test_another_live_windows_line_waits_for_that_window` |
| A torn last line (crash mid-append) | Skipped by readers. The next line starts on a fresh line. | `test_a_torn_last_line_is_skipped_and_the_next_line_starts_fresh` |

**Decision: mark failed, with roll-back only as the fallback.** The journal stays
append-only, so concurrent windows never race a truncate. The one-line success path the
task asks for holds: a landed write has exactly one line, and only a failure adds one.

## Undo and redo

- Every unit an apply commits is stamped `meta.hub = <event id>` before the commit.
  This covers the edit units, the docs/160 wholesale unit, and the units of a `/save`
  that this press lands (`meta.saved`). The wholesale unit is built before the write and
  listed in the event's `units` only when it exists, so an event never names a unit that
  was never journaled.
- A live Ctrl+Z records `undoes=[{"event": unit.meta.hub, "units": [unit id]}]` and
  stamps `meta.hub_undo`.
- A redo records `undoes=[{"event": unit.meta.hub_undo, "units": null}]` and re-stamps
  `meta.hub`.
- A staged undo or applied-log ✕ that a later Apply lands is linked from its
  `jrn:`/`alr:` change-log group.
- A unit applied before S4 has no stamp, so its undo links nothing: honest, not
  guessed.

`recompute_undo_flags` walks the SM events newest to oldest and sets, *as of now*:

- `UNDONE` (8): every unit of the event is taken back by an undo that is not itself
  UNDONE;
- `PARTLY_UNDONE` (16): some of them are.

So Ctrl+Z of one of two edits gives PARTLY, the second gives UNDONE, and a redo puts it
back to PARTLY (pinned on the real routes and on synthetic events).
`REVERTS_TO_EARLIER` (1) is the S3 fact on the SM event's own `state_hash`.

## The build breaks for an unrecorded door

`TestEveryDoorRecords` parses every module of the package (AST, not regex) and fails
on any of these:

1. an `apply_to_live(...)` call without `record=`, or with `record=None`, and any
   call site outside the four-door table. A new door must name its record *and* be
   listed. The keyword stays optional at runtime so that `working_copy`'s own unit
   tests keep calling it bare.
2. a `Saver.save` call site not classified: working copy, or the CLI's journalled
   live save;
3. a `write_state_wiring` / `write_state_wiring_bytes` call site not classified;
4. a write naming `state.json` or `wiring.json` outright (`atomic_write_json`,
   `write_text`, `copy2`, ...) not classified;
5. an autofit `ChipHandle(...)` without `hub_dir=`, or a real-chassis handle with
   `hub_dir=None`.

## Changes outside the new modules

- `core/working_copy.py`, `apply_to_live(record=None)`: commit after the last gate,
  failure marking, `landed()`. The no-op adopt records `skipped`.
- `core/saver.py`, `last_cleared`.
- `core/hub_store.py`:
  - additive tables `sm_events` and `sm_anchors` (`CREATE IF NOT EXISTS`;
    `schema_version` stays 1, and the S3 tests and ledgers are unaffected);
  - `append_sm` (`BEGIN IMMEDIATE`, idempotent per journal id);
  - `recompute_undo_flags`;
  - `state_at` for SM events and SM anchors;
  - `put_blob(level=)`;
  - a 30 s busy timeout;
  - an index on `events.state_hash` (the docs/270 P3 lookup).
- `core/hub_build.py`: an SM head (no root, run id or experiment) no longer crashes
  the resume. A run that is identical to the SM write is a zero-change event (pinned).
  A run older than an SM head still refuses: that is late insertion, which is S5.
- `web/routes.py`:
  - the door wiring above;
  - `ctx["staged_from"]`, set by the two stage routes and cleared with `staged_base`;
  - `_wholesale_unit(after=, cap=)`;
  - `_wholesale_journal_unit`, split out of `_journal_wholesale_commit`. The docs/160 B
    unit is now built *before* the write, in the same tree walk as the event's entries
    when there are no tray edits to exclude. The event therefore lists exactly the
    units that exist, and the door commits that same unit after the write lands. This
    is one walk, where before there was one after the write.
- `core/autofit/writer.py`: `ChipHandle.hub_dir` and `hub_plan`, `_pending`.
- `cli.py`: `set --save` journalled; `--instance`.
- `web/app.py`: `testing=True` projects inline, so a test reads the ledger right after
  the write.

## Performance

The harness is `scratchpad\hub4\perf_apply.py`. It builds the app in **production
mode** (`testing=False`, so the projector runs off the request thread) against a copy of
the chip, with the network forced to `127.0.0.1:1`. Each iteration makes one
`/field/edit` of a T1 value, then a timed `POST /state/apply-to-live`. There are 3
warm-up presses. Base = `219da644` in a detached scratch worktree. Base and new runs
were interleaved, one round each in turn, on this PC, with nothing else running.

**End to end, Apply (ms).**

| Chip | Round | base p50 / p95 | S4 p50 / p95 |
|---|---|---:|---:|
| rC copy, 0.9 MB, 40 presses, 0.3 s pace | 1 | 219.1 / 244.9 | 213.9 / 317.4 |
| | 2 | 207.6 / 242.4 | 204.6 / 256.6 |
| | 3 | 202.0 / 235.3 | 209.8 / 243.8 |
| big30x copy, 18.5 MB, 20 presses, 1 s pace | 1 | 2533.5 / 3282.1 | 2543.4 / 3413.7 |
| | 2 | 2703.7 / 3561.2 | 3253.4 / 4471.0 |
| | 3 | 3070.6 / 4435.2 | 2968.7 / 3446.8 |

- rC: the p50 difference is within ±7 ms across rounds. The one high p95 (317 ms) is a
  single round; rounds 2 and 3 are +14 and +9 ms.
- big30x: the base itself drifts 2.5 -> 3.1 s across rounds, so end to end cannot
  resolve a 10 ms budget there.

**What recording itself adds, measured directly.** Wrapped `Pending.commit` +
`Recorded.landed`: the entries, the journal line and its fsync, and the hand-off. Same
harness, S4:

| Chip | record() p50 | p95 | max | projector (off-thread, wall) p50 / max |
|---|---:|---:|---:|---:|
| rC | 3.53 ms | 4.99 ms | 6.44 ms | 24 ms / 110 ms |
| big30x | 3.88 ms | 4.62 ms | 5.99 ms | 190 ms / 859 ms |

The door-side cost is under 4 ms p50 on both chips, inside the ~+10 ms budget, and it
does not grow with the chip.

Nothing is added on the *edit* path. docs/265 took the tray's file write off the request
thread, and that stays as it is. The one synchronous write S4 adds is the fsync'd
journal line, and only on a press that already writes the chip.

The projector never parses the chip on the write path. The door hands it the written
document reduced to the entries' roots (`hub_entries.post_fragments`, taken under the
store lock, minus edits that landed after the save). Hashing and the anchor's gzip
release the GIL. Its big30x wall time is mostly waiting for the GIL behind the request
thread's own snapshot work. The max is the first, anchored event: a 1.6 MB level-1 gz
of the 18.5 MB pair.

Consecutive SM writes chain, so only 1 anchor was stored in 23 big30x events (1.69 MB
of blobs) and 1 in 43 rC events (87 KB). Pinned by
`test_an_edit_landing_after_the_save_is_not_recorded_as_written`, which makes the
chip-parse fallback raise.

## Tests

The scope was every test file that mentions apply_to_live, sync, restore, take/keep live,
undo/redo, the autofit writer, approvals, dataset apply or `hub_`: 233 files, found by
grep. Base runs used the `219da644` scratch worktree.

| Run | Result |
|---|---|
| base, the first 97 files (narrow grep) | 1 failed, 3195 passed, 40 skipped. The failure is `test_agent_runs::TestRun::test_attribution_never_takes_an_old_run`; it passes in isolation and in every S4 run. |
| base, the other 136 files | 3364 passed, 43 skipped, 0 failed |
| S4, first run of the 97 files + `test_hub_record` | 3 failed: the three fakes below |
| **S4, final, all 233 files on the final code** | **2 failed, 6609 passed, 83 skipped** (37 min, with the browser rig running alongside). Both failures pass 3/3 in isolation, and neither touches the hub. `test_one_run_instant::TestRekeyMigration::test_revert_keeps_a_label_written_after_the_rekey` is the flake docs/270 already recorded. `test_safe_io::TestTwoWritersOfOneFile::test_concurrent_writes_of_one_file_all_land` found a stray Windows `~RF*.TMP` replace temp in its tmp dir under load; `safe_io` is unchanged. |

`tests/test_hub_record.py` has **51 pins**. Each of the three existing tests below was
edited by exactly one signature, and each is justified because this step changes the
call it fakes. Their fakes of `working_copy.apply_to_live` had a fixed signature
`(wc, *, force=False)`. Every door now also passes `record=`, so the fakes accept and
forward `**kw`, and what each test asserts is unchanged:

- `tests/test_auto_apply.py::TestFlush::test_never_forces`: the spy forwards `**kw`.
- `tests/test_state_sync_modes.py::TestConflictPullFlow::test_apply_reconflict_returns_conflict_and_keeps_stash`.
- `tests/test_web.py::TestApplyHardeningR16::test_unexpected_apply_failure_answers_honestly`.

No other existing test was edited.

## Mutations

The script is `scratchpad\hub4\mutate.py`. It applies one source edit at a time, runs
the named pin(s) with the mandated interpreter and flags, and restores the source bytes
in `finally`. A collection or syntax error does not count as RED. Machine-readable
results: [271_hub_record_mutations.json](271_hub_record_mutations.json).

**59 / 59 RED, and all 51 pins are targeted.** By area:

- **Write-ahead protocol:**
  - commit after the write;
  - no fsync;
  - no failure mark;
  - an unverified write counted;
  - commit before the gates;
  - no chip -> skip;
  - no roll-back;
  - a torn line breaks the next;
  - blob in a sub-directory;
  - an orphan trusted;
  - an orphan's later-base evidence ignored;
  - a peer's line not waited for;
  - the diff computed before the no-change skip;
  - a no-change write recorded;
  - a refusal that records.
- **Rows and replay:**
  - no list lift;
  - revert forgets `created`;
  - in-place `_set`;
  - delete sets null;
  - fragments keep a late edit;
  - the projector parses the chip;
  - never anchor / always anchor;
  - derived replay skips its entries;
  - an UNDONE undo still counts;
  - no PARTLY;
  - no REVERTS flag;
  - the builder's SM head;
  - the async projector run inline.
- **Doors:**
  - each door unrecorded, or with its actor / plan / src / kind / run / undo link /
    unit stamp / `staged_from` / restore `after` / wholesale decision lost;
  - a phantom unit listed;
  - the shared core's unit not listed;
  - the `alr:` link lost;
  - the autofit `by` lost;
  - the CLI bypassing the journal;
  - `Saver.last_cleared` lost;
  - catch-up on open lost;
  - Take live / pull recording.
- **Fault injection:**
  - a new unrecorded `apply_to_live` door;
  - a new pair writer;
  - a new named `state.json` writer;
  - a `ChipHandle` without `hub_dir`;
  - the real autofit handle with `hub_dir=None`.

The first sweep found 3 non-RED results, all in the harness or the pins:

1. two anchors were not unique or did not balance;
2. a rebuild mutation also changed the ledger it was compared with, so it could never
   show. It was replaced by one that breaks only the journal-only path;
3. the generator for `test_revert_and_apply_are_inverse` used `apply_entries` itself,
   so it agreed with any mutation of it. It now edits documents with its own plain
   navigation.

Two more pins (the shared core's unit list, the `alr:` link) were added after the
sweep showed they were only covered indirectly. The final sweep was 59/59.

## Real browser

The rig was SM served from this worktree on **5131** (waitress, `cqt`) against a
**copy** of `D:\work\sm_qa_rigs\agent\rC\chip`:

- network `127.0.0.1:1`;
- its `extras.data_folder` re-pointed at the rig's own `data`, so nothing was written
  beside rC data;
- a sandboxed `HOME` / `USERPROFILE` and qualibrate config;
- one synthetic dataset run that moves `qubits.qA2.f_01` by +1.25 MHz.

Headless Chrome ran over CDP on **9451**, driven by `tests/browser/journeys/cdp.cjs`
with real mouse and keyboard events. The person named themself through the `sm_actor`
cookie (a generic `operator`). After each press the journal and ledger were read by `scratchpad\hub4\hub_read.py`
(`hub.summary`). Result: **ALL OK, console errors 0**.

| Step | Real action | Journal + ledger read back |
|---|---|---|
| Live State Edit | typed `4.321e-05` into qA1 T1, Enter, clicked the tray Apply | `sm_apply` / `pull_apply`, actor `human:operator`, `t` 14:32:49.663+09:00; entry `qubits.qA1.T1` 3.5e-05 -> 4.321e-05 (`by` human:operator); ledger row `set` 3.5e-05 -> 4.321e-05; live file = 4.321e-05 |
| Ctrl+Z | the keystroke on the page | `undo` / `ctrl_z`, `undoes` = the Apply's event + unit; entry 4.321e-05 -> 3.5e-05; the Apply's ledger flags = 8 (UNDONE); live file back to 3.5e-05 |
| Datasets -> run -> State -> **Apply to chip** | real click | `sm_apply` / `dataset_apply`, `run_uid` `c3a2b7dc:1`, actor `human:operator`; entry `qubits.qA2.f_01` 5714586444.185409 -> 5715836444.185409; one ledger row; live file = the run's value |
| outside write, then sync control -> **Take live** | real click | still 3 journal lines; SM's working copy holds the outside value; pill "Took live" |
| back + reload | | page intact |

The screenshots (read) are in `scratchpad\hub4\shots\`:
`1_edit_in_tray`, `2_after_apply`, `3_after_ctrl_z` ("Undone -> live"), `4`/`5` (dataset
State tab, then "Run #1's state is now LIVE"), `6_sync_panel`, `7_after_take_live`,
`8_reload`.

The first attempt picked the wrong run. The chip's declared data folder (rC's own data)
made its run #1 the first `:1` row, and the identity gate rightly answered 409. The rig
was rebuilt with its own data folder and the journey re-run from scratch. Only what was
started was stopped: the server and Chrome. The rig copy, the perf copies and the
Chrome profile were deleted (absolute paths).

## Open items for S5 and later

1. **Late insertion of runs.** The ledger now holds SM events whose `ord` is the
   projection order. A run older than the newest SM event still makes the offline
   builder refuse ("unknown run precedes ledger head ... late insertion is S5"). S5 must:
   - insert runs and SM events in instant order;
   - re-diff the successors;
   - keep SM rows as recorded (they are facts, not diffs);
   - decide when an SM event's base was a run SM had not ingested yet. Today that event
     is an anchor, so `state_at` stays exact.
2. **Catch-up of runs on chip open**, which is S5 proper. This step only catches up the
   SM journal on open.
3. **Run folders rewritten after the run** (docs/270 review, defect 5). The new edge
   for S5 is a rewrite *after* an SM write that landed in between. The SM event stays a
   fact; the run's correction event must be ordered after it and must not erase it.
4. **The Experiment Runner's human items save the live chip themselves.** They are runs,
   so they need real-time run ingestion, or the next SM write's base will not chain
   (it is anchored meanwhile).
5. **Two windows on one chip.** The ledger order of their SM events is projection order
   (`BEGIN IMMEDIATE`), and a window's line waits at most a minute for the other
   window's projector (then the next write or open). Fine for one chip and one person;
   S5's ordering by `t_utc_us` should replace the append rank.
6. **`external_observed`** is not built (optional, and not trivial: see above).
7. **The chip identity changing inside a write.** A run state that names another chip
   (`extras.chip_name`): the event is filed under the chip dir resolved *before* the
   write. A later chip-dir move does not carry `events.jsonl` with it.
8. **Units from before S4** have no `meta.hub`, so undoing them links nothing (honest).
   A staged-only Ctrl+Z (live walk OFF) followed by a manual Apply links through the same
   `jrn:` group rule as the `alr:` revert. That rule is pinned through the `alr:` route;
   the staged-only path has no route pin of its own yet.
9. **`HubStore()` loads every path id on open, and writes `meta` on open** (docs/270
   P3). The projector opens it once per run of the tail. That is fine for SM-only
   ledgers. Keep one connection per chip in the projector before S5 puts 100k-path run
   ledgers behind it.
10. **`REVERTS_TO_EARLIER` is byte-based** (docs/270 P3). The same content serialized
    differently is not flagged.
11. **`OVERLAPS_SM_WRITE`** (DESIGN 3.4: `run_start < sm_event.t < created_at`) needs
    runs in the same ledger, so it belongs with S5's ingestion. The SM side of it is
    ready: every SM event carries its own UTC instant.
12. **Bit assignment.** S3 uses 1/2/4. S4 adds UNDONE 8, PARTLY_UNDONE 16 and
    DERIVED 32. `schema_version` stays 1, because the tables are additive. A reader
    that predates S4 would not understand the SM rows. There is none outside this
    branch.
