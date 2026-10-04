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

Each write line gets one outcome line after the write (see *Failure modes* and the
*Review round*):

- `{"landed": <id>}` once the write is verified. It is flushed, not fsync'd: it is as
  durable as the live write it describes, and a rebuild that lacks it falls back to
  evidence. A second read that found the written content adds `"verified": "second
  read"`; a write found landed later by a no-op press adds `"late": true`.
- `{"failed": <id>, "error": ...}`, fsync'd, when the chip still holds what it held
  before.
- none when nobody can tell (the chip unreadable, or holding neither the old content
  nor the new): evidence decides at projection time.

Every append, and the roll-back truncation, holds the journal's cross-process lock
(`history/<chip>/events.lock`) and reads its line back.

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
  written (`hub.wholesale_entries` -> `hub_entries.tree_entries`, with the one equality
  `hub_rules.same`: `1 == 1.0`, never `1 == True`). This applies whenever the working
  copy carries content the log does not name. That is decided **by content**, not by a
  flag: the working pair's stat fingerprint taken before the press's own save must equal
  `WorkingCopy.synced_working_fp`, the fingerprint recorded at the last sync point
  (create, pull, apply). Anything that wrote the working files since -- `/save`, a
  stage, an Auto-Calibrate review-autonomy save -- fails it, as do a staged version, a
  forced push (Keep mine) and an empty log. The difference is computed in
  `Pending.prepare`, before `apply_to_live`'s last staleness re-check and outside the
  chip lock, from bytes SM already read. A write whose content equals what the chip
  already holds is not an event, and its diff is never computed.

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
| Any failure after the line | Settled by **reading the chip once more**: the written content -> `landed` (`"verified": "second read"`), and when its mtimes read back the press succeeds; the old content (`base_hash`) -> `failed`; anything else (unreadable, a half pair, a write overwritten before the verify) -> no outcome line, evidence decides. A landed write is never marked failed. | `test_a_landed_write_whose_verify_read_failed_is_recorded_landed`, `test_a_half_failed_pair_whose_content_is_whole_landed`, `test_a_true_half_pair_is_left_to_evidence_never_marked_failed`, `test_an_overwritten_write_is_left_to_evidence`, `test_a_write_found_unchanged_is_marked_failed` |
| A write marked failed that had landed after all | The next press that finds the chip already holding that content (the docs/116 no-op adopt) writes a late `landed` line for it, and the ledger relabels the event `landed` in place (same `ord`). | `test_a_write_marked_failed_that_had_landed_is_relabelled_by_the_next_noop` |
| Even the failure line cannot be written | The event's own line is truncated back out, under the journal lock, which is only possible while it is still the last line. Otherwise the loss is logged critically, and the in-process projector, which knows the outcome, still records it as failed. | `test_unmarkable_failure_rolls_the_line_back_out`, `test_a_failure_mark_truncation_never_cuts_another_writers_line` |
| SM stops between the line and the outcome | At catch-up a line with no outcome line, whose writer is gone, is `landed` only on evidence: a later line was taken against its `post_hash`, or the chip holds it now. Otherwise it is `unconfirmed`: an event with an error and no rows. | `test_lines_nobody_can_vouch_for_are_decided_by_evidence` |
| A line whose writer process is still alive (another SM window, the CLI, this process's own in-flight write seen by another projector) | Never guessed. It waits for that writer's outcome line, retried every 2 s, for at most `WRITER_GRACE_S` = 600 s from the line's own time (a reused pid cannot stall it). | `test_another_live_windows_line_waits_for_that_window`, `test_a_cli_line_in_flight_waits_for_its_writer`, `test_a_reused_pid_cannot_stall_a_line` |
| A torn last line (crash mid-append) | Skipped by readers. The next line starts on a fresh line. | `test_a_torn_last_line_is_skipped_and_the_next_line_starts_fresh` |
| The journal shortened or replaced behind the ledger's offset | An offset past the end of the file, or not on a line boundary, is not trusted: the whole journal is rescanned (projection is idempotent per line id). | `test_a_shortened_journal_is_rescanned`, `test_an_offset_not_on_a_line_boundary_is_rescanned`, `test_a_rolled_back_line_behind_the_offset_does_not_hide_the_next` |
| One line the projector cannot project | Stored as an event with `error = "projection error: ..."` and no rows; the lines after it still project. Diagnostics shows a `history_ledger` warning naming it. | `test_one_unprojectable_line_is_kept_with_its_error_and_the_tail_moves_on`, `test_diagnostics_names_a_write_the_history_could_not_take_in` |

**Decision: mark the outcome, with roll-back only as the fallback.** The journal stays
append-only apart from that fallback, which runs under the journal lock and only while
the line is still last. A landed write has its fsync'd event line plus one flushed
`landed` line, so a rebuild from the journal alone agrees with the live ledger (review
P1-3); only the event line is on the request thread's fsync path.

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

Two layers since the review round (P2-1).

**Static: every call site, per site.** `TestEveryDoorRecords` parses every module of
the package (AST) and classifies each call site that can put bytes into a chip folder
by (file, innermost function, kind), with its exact count, in `DOOR_SITES`:

| Kind | What the scanner matches |
|---|---|
| `apply` | `apply_to_live(...)` and any alias of it (`import ... as`, an assignment). Must name `record=`, never `None`, never a `hub.unrecorded(...)` call. A bare reference that is not a call (a callback) is reported as a door the scan cannot follow. |
| `unrecorded` | `hub.unrecorded(...)`: allowed only at documented sites (the simulator's handle). |
| `save` | any `.save(` whose receiver is not an instance-dir store (`agent_session`, `entity_notes`, `limits`, ...): `Saver(store).save()`, `sv.save()`, `machine.save()`. |
| `pair` | `write_state_wiring[_bytes]` outside `safe_io`. |
| `named` | a file write, copy or move (`atomic_write_json`, `write_text`, `copytree`, `os.replace`, an `open(..., "w")`, ...) whose arguments or receiver name `state.json`, `wiring.json`, `*live*`, `state_path`, `quam_state*`, `chip_dir`, ... |

A new site, or a second call inside a classified function, is not in the table, so the
file fails until someone decides what the write is. Fault injection is part of the
suite: the seven doors the review planted -- `Saver(store).save()` on a live folder,
the same through a variable, an `atomic_write_json` to a `state_path`, an `as`-aliased
`apply_to_live`, a `copytree` into a live folder, a second state/wiring write inside the
classified stage route, a door passing `record=hub.unrecorded(...)` -- plus a callback
reference are each planted in a copy of the sources and must be reported
(`test_a_planted_new_file_door_is_reported`,
`test_a_planted_edit_to_a_classified_function_is_reported`).

**Runtime: the guard.** `apply_to_live(record=None)` goes through
`hub.guard_unrecorded`. In the test suite (`tests/conftest.py` sets `SM_HUB_STRICT=1`)
it raises `RecordError` before the chip is touched, so a door the scanner missed still
breaks the build the first time a test drives it. In production a person's write is
never refused over bookkeeping: it lands and is recorded as actor `unattributed`, src
`unrecorded_door`, with the whole-chip difference, in the chip's ledger (the app
registers its identity ladder with `hub.set_chip_dir_resolver`), and a warning is
logged. The 13 unit tests that drive a bare working copy with no chip ledger say so
with `record=hub.unrecorded("<reason>")` (`test_working_copy.py` x8,
`test_persistence_staleness.py` x2, `test_refused_apply_changes_nothing.py`,
`test_state_coherence.py`, `test_autofit_plan_writer.py`). Each of those tests drives
the working-copy mechanics (sync points, staleness, refusal), not a door; the edit adds
one keyword and changes nothing they assert.

An autofit `ChipHandle(...)` must still name `hub_dir=`, and the real-chassis handle
must not pass `None`.

## Changes outside the new modules

- `core/working_copy.py`, `apply_to_live(record=None)`: `prepare` before the lock, the
  re-checks and `commit` under the chip's cross-process write lock, a post-fsync mtime
  re-check, the outcome settle by a second read, `landed()`. The no-op adopt records
  `skipped` and calls `adopt_late`. `WorkingCopy.synced_working_fp` (persisted in the
  meta sidecar) is the content check's anchor; the fingerprint of the working pair it
  read goes to `prepare`, which checks it against the door's `Pending.expect_fp`.
- `core/xlock.py` (new): an OS lock on a lock file (`msvcrt.locking` / `fcntl.flock`)
  behind a per-process lock, re-entrant per thread. Two locks use it: the journal lock
  `history/<chip>/events.lock`, and the machine-wide live-chip lock
  `%TEMP%/quam-sm-locks/live-<sha1 of the resolved folder>.lock` (never in the chip
  folder).
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
  the write; `hub.set_chip_dir_resolver` for the production guard.
- `core/diagnostics.py`: a "Change history complete" check in the catalogue (domain
  Other); `web/routes.py` `_hub_ledger_findings` emits it.
- `tests/conftest.py`: strict mode for the unrecorded-door guard.

## Performance

Measured on this PC with other work running, so end-to-end numbers drift by more than
the budget between runs; the door-side cost is measured directly. Harnesses:
`scratchpad\rv4\probe_record.py` (per press: `Pending.prepare`, `Pending.commit`,
`Recorded.landed`, each journal append), `perf_apply2.py` (end to end) and
`perf_doors2.py` (the wholesale doors), all in **production mode** (`testing=False`,
the projector off the request thread) against a copy of the chip with the network forced
to `127.0.0.1:1`. Base = `219da644`, S4 = `e38602f9`, both in detached scratch worktrees.

**What recording adds to a plain edit -> Apply (the review-round code).**

| Chip | presses | record() p50 | max | of which: prepare / commit / landed (p50) | whole-chip diffs |
|---|---:|---:|---:|---|---:|
| rC copy, 0.9 MB | 30 | 4.16 ms | 5.28 ms | 0.05 / 2.5 / 1.6 ms | 0 |
| big30x copy, 18.5 MB | 15 | 5.33 ms | 21.6 ms (one 18.7 ms fsync) | 0.05 / 2.6 / 2.1 ms | 0 |

Inside the ~+10 ms budget on both chips and flat in the chip size. The `landed` line
(~1.5-2 ms: the journal lock, a flushed append, its read-back) is the one cost the review
round added to the success path. End to end in the same session: rC base 274.5 ms vs
new 219.4 ms p50 (40 presses each; the order of the difference is the session's drift,
not the change), big30x base 2687 ms vs new 2882 ms (20 presses each, the same drift).

**The wholesale doors** (`/save` then Apply, and a staged version then Apply), p50 ms:

| Chip | Door | base end to end | S4 end to end / **commit** | new end to end / **commit** / prepare |
|---|---|---:|---:|---:|
| rC | saved | 158.5, 137.3 | 168.7, 151.6 / **17.7, 16.5** | 166.0, 118.7 / **3.7, 1.7** / 7.2, 5.8 |
| rC | staged | 136.2, 125.6 | 148.5, 128.3 / **16.6, 13.4** | 137.6, 120.1 / **3.2, 1.7** / 10.8, 14.0 |
| big30x | saved | 2250.2 | 2353.0 / **288.7** | 2518.5 / **4.6** / 125.5 |
| big30x | staged | 2022.2 | 1766.5 / **213.5** | 1897.8 / **4.1** / 269.2 |

"Commit" is the stretch between `apply_to_live`'s last staleness re-check and the live
write, i.e. what widens the window another writer can slip into; under the chip lock it
is now only the journal append. The whole-chip difference these doors need (the content
check says the log does not name everything) moved into `prepare`, before the re-check
and outside the lock. It is still paid on the request thread: big30x ~125 ms for a
saved press, ~270 ms for a staged one (that one also builds the docs/160 B undo unit,
which base built after the write). A plain edit -> Apply never pays it (0 of 45 presses
above).

**Entries -> rows** (`post_fragments` + `rows_for`, best of 3, a 20 000-qubit document):

| Entries | S4 | review round |
|---:|---:|---:|
| 500 | 65.4 ms | 10.1 ms |
| 2 000 | 1 006.7 ms | 46.7 ms |
| 5 000 | 7 214.1 ms | 119.6 ms |

The projector never parses the chip on the write path: the door hands it the written
document reduced to the entries' roots (`hub_entries.post_fragments`, taken under the
store lock, minus edits that landed after the save). Pinned by
`test_an_edit_landing_after_the_save_is_not_recorded_as_written`, which makes the
chip-parse fallback raise. Projector wall time (off the request thread) in the
instrumented runs: rC p50 30 ms, big30x p50 223 ms.

Nothing is added on the *edit* path. docs/265 took the tray's file write off the request
thread, and that stays as it is.

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

**Review round.** The scope was the review's own list (`scratchpad\rv4\files.txt`: every
test file touching the working copy, apply, the autofit writer, the undo journal,
auto-apply, sync, restore, dataset apply and the CLI -- 70 files) plus the five files
this round edits or adds to (`test_hub_record`, `test_persistence_staleness`,
`test_state_coherence`, `test_diagnostics_tier2`, `test_diagnostics_ui`), run from
`tests/` in a scratch worktree holding exactly this round's code (node + jsdom present).

| Run | Result |
|---|---|
| base `219da644`, the 70 files (the review's run) | 9 failed, 2038 passed, 58 skipped |
| S4 `e38602f9`, the 70 files (the review's run) | the same 9 failed, 2038 passed, 58 skipped |
| review round, the 75 files, before the shared-working-copy fix | 8 failed, 2254 passed, 37 skipped (20 min) |
| **review round, the 75 files, final code** | **7 failed, 2258 passed, 37 skipped** (29 min, alongside the mutation sweep and the browser race) |

All 7 are the base's own failures, identical on base: 2 x `test_column_history`,
3 x `test_live_replace_routes::TestTheBadgeAndTheBannerAgree`,
`test_unseen_edits::...test_the_poll_is_wired_to_the_decision`,
`test_web_needs_no_cli::test_field_edit_works_with_typer_blocked`. No failure is new.
Two base failures (`test_apply_ux` and `test_undo_nav` selfchecks) pass here because
jsdom was present. The earlier run's 8th,
`test_safe_io::TestTwoWritersOfOneFile::test_concurrent_writes_of_one_file_all_land`, is
the `~RF*.TMP` flake recorded above: `safe_io` and its test are unchanged by this branch,
and in isolation it failed 3/15 here and 1/15 on base with the same stray
`ws_cache.json~RF*.TMP`. The four hub test files: **151 passed** on the final code, twice.
In a third run, after the dep suite, `test_two_processes_pressing_apply_never_both_land`
failed once and its message was not kept (the output was filtered to the summary line).
It then passed 36 times in isolation, some of them under parallel test load (three
more launches never started: no output, and where the exit code was kept it was 127
from the shell -- a process-spawn failure on this loaded PC, not a test result), and in
the next full-file run. Its assertions now carry every
round's outcome and the journal's landed lines, so a recurrence explains itself.

`tests/test_hub_record.py` has **88 pins** (51 at `e38602f9`). Each of the three existing tests below was
edited by exactly one signature, and each is justified because this step changes the
call it fakes. Their fakes of `working_copy.apply_to_live` had a fixed signature
`(wc, *, force=False)`. Every door now also passes `record=`, so the fakes accept and
forward `**kw`, and what each test asserts is unchanged:

- `tests/test_auto_apply.py::TestFlush::test_never_forces`: the spy forwards `**kw`.
- `tests/test_state_sync_modes.py::TestConflictPullFlow::test_apply_reconflict_returns_conflict_and_keeps_stash`.
- `tests/test_web.py::TestApplyHardeningR16::test_unexpected_apply_failure_answers_honestly`.

No other existing test was edited in S4. The review round edits:

- 13 bare `apply_to_live(wc)` calls in 5 unit-test files gain
  `record=hub.unrecorded("<reason>")` (see *The build breaks for an unrecorded door*);
- `tests/conftest.py` turns strict mode on;
- in `test_hub_record.py`, three S4 pins follow the new protocol:
  `test_one_line_is_durable_before_the_live_write` now also expects the `landed` line;
  `test_another_live_windows_line_waits_for_that_window` fakes `_pid_alive` (the peer
  registry it faked is gone: any live writer pid is waited for) with a line of now;
  `test_an_unverified_write_is_marked_failed` became two pins, because an overwritten
  write is no longer called failed (`test_an_overwritten_write_is_left_to_evidence`,
  `test_a_write_found_unchanged_is_marked_failed`). The four classification tests of
  `TestEveryDoorRecords` became the per-site scanner and its plants.

## Mutations

The script is `scratchpad\hub4\mut_rv.py` (the S4 list of `mutate.py` with its anchors
moved to the review-round code, plus one or more mutations per review fix). It applies
one source edit at a time (some are two-site edits), runs the named pin(s) with the
mandated interpreter and flags, and restores the source bytes in `finally`. A collection
or syntax error does not count as RED. Machine-readable results:
[271_hub_record_mutations.json](271_hub_record_mutations.json).

**91 / 91 RED** in one final sweep on the final code. The S4 59 (12 re-anchored to the
new code, 5 re-pointed at the per-site scanner) and 32 new ones:

- **P0-1:** the live lock off; the post-fsync re-check off; both off (the two-process
  pin: both windows landed in 10 of 25 rounds);
- **P0-2:** the journal append unlocked; the roll-back truncation blind to later lines;
- **P1-1:** the content check always true (routes), always false (routes), always true
  (autofit writer); the sync point's fingerprint not advanced after an apply;
- **P1-2:** the second read ignored; a half pair marked failed; the late landed mark off;
  the relabel off;
- **P1-3:** no landed line; the journal's landed marks ignored by a rebuild;
- **P2-1:** strict mode off; the production `unattributed` record off;
- **P2-2:** `prepare` skipped (the diff then runs inside the commit);
- **P2-3:** the offset trusted; the line boundary unchecked;
- **P2-4:** this process's own in-flight line never waited for; the grace unbounded;
- **P2-5:** one bad line stops the tail; the Diagnostics finding off;
- **P2-6:** discarded units not marked; discarded units still claimed;
- **P3:** `!=` instead of `hub_rules.same`; a quadratic `_minimal`;
- **the shared working copy (browser race):** the bytes check off; the Apply door not
  naming its save's fingerprint; the autofit writer not naming it; the writer taking it
  before its save instead of after.

An earlier sweep (87 mutations, before the shared-working-copy fix) found 2 GREEN,
both weak pins, not weak code: the mid-line offset pin
cut a line that was already projected (so skipping it lost nothing), and the CLI pin was
healed by the late relabel even without the wait. The first pin now cuts an unprojected
line; the second now asserts that the in-flight line is not projected while its writer
runs. Both re-ran RED, as did the two neighbouring mutations. Of the four mutations added
with the shared-working-copy fix, one (the fingerprint taken before the writer's save)
was GREEN at first: it only sends every autofit write down the by-content path, which
is exact but loses the `by="autofit"` stamp; the writer pin now asserts that stamp.

**S4's own sweep (59 / 59)** is superseded by the list above; its history is unchanged:
the first S4 sweep found 3 non-RED results, all in the harness or the pins (two anchors,
a rebuild mutation that changed the ledger it was compared with, and a generator that
used `apply_entries` itself).

## Real browser

The rig was SM served from this worktree on **5131** (waitress, the test conda env) against a
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
| Datasets -> run -> State -> **Apply to chip** | real click | `sm_apply` / `dataset_apply`, `run_uid` `c3a2b7dc:1`, actor `human:operator`; entry `qubits.qA2.f_01` `<f>` -> `<f>` + 1.25 MHz; one ledger row; live file = the run's value |
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

## Review round

An adversarial review of `e38602f9` returned 13 executed refutations (repros in
`scratchpad\rv4\`: `test_zz_review_repro.py`, three race scripts, `perf_doors.py`,
seven planted doors). The fixes are new commits on top of `e38602f9`, which stays as
it is (a later branch is built on it). Every repro is now a permanent pin in
`tests/test_hub_record.py` (class `TestReviewRound`, plus `TestEveryDoorRecords` and
`TestJournal`); each pin was mutation-checked (see *Mutations*).

| Finding | Fix | Pin(s) | Measured |
|---|---|---|---|
| **P0-1** Two windows pressing Apply together could both land: the journal's fsync sat between the last staleness re-check and the write. | The entries are prepared before the re-check (`Pending.prepare`). A machine-wide per-chip OS lock (`xlock.live_lock_path`) is held from the re-check through the write and its verify, so a second SM process waits and then re-checks. After the fsync the live mtimes are re-read; a move with new content is refused as stale and the line is marked failed. | `test_a_window_holding_the_chip_lock_is_waited_for_then_refused`, `test_an_outside_write_during_the_journal_fsync_is_refused_not_overwritten`, `test_two_processes_pressing_apply_never_both_land` | `race_both_land.py`, 300 rounds, rounds where both landed: base **1**, S4 **155**, review round **0** (two runs: 0 and 0). Real browser, two SM processes: 0 of 6 rounds (below). |
| **P0-2** The journal append was not atomic across processes (Windows append mode is seek-then-write). | A cross-process lock (`history/<chip>/events.lock`) around every append and the roll-back truncation; the line goes at the locked end of the file and is read back before it counts. | `test_concurrent_processes_append_without_loss_or_tearing`, `test_a_failure_mark_truncation_never_cuts_another_writers_line` | `race_append.py` 4 x 300: S4 1125/1200 parsable, **36 torn**; review round 1200/1200, 0 torn (and 8 x 300: 2400/2400, 0 torn). `race_windows.py` 300 rounds: S4 lost 1 line (a landed write); review round 300 claimed = 300 in the journal, 0 lost. |
| **P1-1** Content the change log did not name (an Auto-Calibrate review-autonomy save in the same working copy) landed unrecorded. | Decided by content: the working pair's stat fingerprint before the press's save must equal `synced_working_fp` (the sync point's); otherwise the entries are the whole-chip difference. In the routes (`_hub_content_ok`) and the autofit writer (`_save` / `_pending`). | `test_a_review_save_then_a_person_apply_records_everything_that_lands`, `test_the_autofit_writer_records_a_review_save_it_later_applies` | Both repros green: the landed paths equal the recorded paths, `state_at` equals the chip. |
| **P1-2** A write that landed but whose verify read failed was marked failed and never recorded landed (also a half-failed pair whose content was whole). | The failure branch reads the chip once more: the written content -> landed (`"verified": "second read"`; the press succeeds when the mtimes read back), the old content -> failed, anything else -> left to evidence. A no-op press that finds the chip holding a write's content writes a late `landed` line for it, and the ledger relabels it in place. | `test_a_landed_write_whose_verify_read_failed_is_recorded_landed`, `test_a_half_failed_pair_whose_content_is_whole_landed`, `test_a_true_half_pair_is_left_to_evidence_never_marked_failed`, `test_a_write_marked_failed_that_had_landed_is_relabelled_by_the_next_noop`, `TestJournal::test_an_overwritten_write_is_left_to_evidence`, `TestJournal::test_a_write_found_unchanged_is_marked_failed` | r3: the first press now answers 200 and its event is landed. r3b: the repro's precondition (`pytest.raises(OSError)`) no longer holds -- the press succeeds because the chip holds exactly the written content; the pin asserts that instead, and a true half pair is a separate pin. |
| **P1-3** A rebuild from the journal alone disagreed with the live ledger (a landed write's outcome lived only in RAM). | A `{"landed": id}` line after the verify (flushed, not fsync'd); the projector reads outcome lines first. | `test_a_rebuild_from_the_journal_alone_agrees` | r12 green: original == rebuilt outcomes. |
| **P2-1** The AST test missed 7 planted doors. | Per-call-site classification with counts (apply / unrecorded / save / pair / named), aliases resolved, `record=unrecorded(...)` at a door reported; plus the runtime guard (strict in the suite, `unattributed` in production). | `test_the_package_has_no_unrecorded_door`, `test_a_planted_new_file_door_is_reported` x6, `test_a_planted_edit_to_a_classified_function_is_reported` x2, `test_an_aliased_door_is_refused_at_runtime_in_strict_mode`, `test_an_unrecorded_door_in_production_is_recorded_unattributed` | All 7 plants (+ a callback reference) reported. The runtime half of r7 cannot cover plants a/b/c/f (a `Saver`, `safe_io` or `shutil` call on a live path is a generic file write); those are covered statically. Plant e (the alias) is refused at runtime too. |
| **P2-2** Save-then-Apply parsed and walked the whole chip on the request thread inside commit (big30x 198-326 ms). | The whole-chip difference runs only when the content check needs it, in `prepare` (before the re-check, outside the lock), from the shared parse. | `test_a_plain_apply_never_parses_the_chip_inside_the_commit` | Commit p50 big30x saved 288.7 -> **4.6 ms**, staged 213.5 -> **4.1 ms**; rC 16.5-17.7 -> 1.7-3.7 ms. The difference itself is still paid on the request thread before the lock: big30x ~125 ms (saved), ~270 ms (staged). See *Performance*. |
| **P2-3** An offset past a shortened journal meant new lines were never projected. | An offset past the end or not on a line boundary triggers a rescan from 0. | `test_a_shortened_journal_is_rescanned`, `test_an_offset_not_on_a_line_boundary_is_rescanned`, `test_a_rolled_back_line_behind_the_offset_does_not_hide_the_next` | r5, r5b green. |
| **P2-4** A CLI write stayed `unconfirmed` (a window projected its line in flight). | A line whose writer pid is alive is waited for -- any pid, including this process's own when another projector reads it -- bounded by `WRITER_GRACE_S` (600 s from the line's time). | `test_a_cli_line_in_flight_waits_for_its_writer`, `test_a_reused_pid_cannot_stall_a_line` | r4 green: the CLI's write is landed. |
| **P2-5** One unprojectable line blocked every later line. | Per-line error capture: the event is stored with `error = "projection error: ..."`, the tail moves on; Diagnostics shows a `history_ledger` warning. | `test_one_unprojectable_line_is_kept_with_its_error_and_the_tail_moves_on`, `test_diagnostics_names_a_write_the_history_could_not_take_in` | r11: 4 of 4 lines in the ledger (the repro asserted 3; the bad line is now stored with its error rather than skipped). |
| **P2-6** A saved edit discarded by Take live was claimed by the next Apply (and kept the event PARTLY_UNDONE). | Units a `/save` committed and no press landed are marked `discarded` whenever the working copy is replaced wholesale (`_rebuild_after_working_copy_replaced`: Take live, stage, restore); saved-unit lists skip them. | `test_a_saved_edit_dropped_by_take_live_is_never_claimed` | r13, r13b green: the event's units exclude the discarded one; after Ctrl+Z it is UNDONE, not PARTLY. |
| **P2-7** This document quoted real chip values and a lab-named conda env. | Replaced with `<f>` -> `<f>` + 1.25 MHz and "the test conda env". | -- | -- |
| **P3** (a) The whole-chip difference treated `1 == True`; (b) `post_fragments` / `rows_for` were quadratic. | (a) `hub_rules.same` in `tree_entries`; (b) prefix-set minimal roots, an in-place sparse copy, copy-on-write apply/revert. | `test_the_whole_chip_difference_never_calls_1_true`, `test_keep_mine_over_a_number_to_bool_change_records_the_row`, `test_rows_and_fragments_scale_near_linearly` | r10 green. 5 000 entries: S4 7 214 ms -> 120 ms. |

**One more, found by the browser race below.** Two SM windows of one install share one
working copy on disk. When window B's save replaced the working files between window
A's save and A's write, A wrote B's content: A's own edit did not land, and its event
named A's edit while the chip took B's -- a record that did not match the bytes. (That
the content is B's is pre-existing SM behaviour for two processes sharing one instance
dir; the wrong record was this step's.) Fix: each door names the stat fingerprint of the
working pair its own save produced (`Pending.expect_fp`, from `press["saved_fp"]` and the
autofit writer's `_save`); `apply_to_live` passes the fingerprint of the pair it actually
read, and when they differ the entries are the whole-chip difference to the bytes
written, no undo unit is claimed and the projector parses the bytes. Pins:
`test_bytes_another_process_saved_are_recorded_by_content`,
`test_a_press_whose_saved_files_were_replaced_records_what_it_wrote`,
`test_the_autofit_writer_records_what_it_wrote_when_its_files_were_replaced`.
Making the two windows' save + write atomic across processes is left open (open item 14).

The two route exactness sweeps of the review (r9b pulse page, r9c staged version plus
tray edits) were green on S4 already and are kept as pins
(`test_route_exactness_the_pulse_page`, `test_route_exactness_a_staged_version_plus_tray_edits`).

**The repro file on the final code:** 15 of its 19 tests pass unchanged. Three pass
after a one-line adaptation each, listed because each changes what the repro asserts:
r5b's stand-in for `Hub._append` must take the new `fsync` keyword; r3b's precondition
`pytest.raises(OSError)` no longer holds (the press succeeds -- the chip holds exactly
the written content and the second read says so); r11 asserted 3 projected lines and 4
are (the bad one is kept with its error). r7 imports the planted module, which is not in
the tree: its plants are pinned statically. With the module in place, plant e (the
alias) was refused at runtime by the strict guard; plants a/b/c/f (a `Saver`, `safe_io`
or `shutil` writing a live path directly) can only be caught by the scanner.

**Real browser, two windows.** Two SM processes (ports 5131 and 5132, one instance dir:
two windows of one SM install) on a **copy** of `D:\work\sm_qa_rigs\agent\rC\chip`
(network `127.0.0.1:1`, its data folder re-pointed into the rig, sandboxed `HOME` /
`USERPROFILE` and qualibrate config), headless Chrome over CDP on **9451**, one tab per
window. Each round each tab typed a different T1 into Live State Edit (real keys), then
both tray Apply buttons were pressed and released at the same instant (the two mouse
releases sent concurrently). Journey: `scratchpad\rv4\race_journey.cjs`; journal and
ledger read after every round by `race_read.py`.

Final run (12 rounds, the final code): **ALL OK, 78 checks, console errors 0.** Window
A's write landed and B was refused in 5 rounds, the reverse in 5, and in 2 the second
press pulled the first write and applied its own edit on top (the tray's Apply is Pull &
apply, so that is a merge, not a lost update). In every round:

- replaying the landed events in journal order onto the chip as it was gives the chip
  as it is -- no lost update, nothing unrecorded;
- each landed write was taken against the one before it (`base_hash` = the previous
  `post_hash`);
- each event records its own window's edit with that window's actor
  (`human:operator_a` / `human:operator_b`), one ledger row each;
- no landed event names the refused window's edit, which is not on the chip.

Window A after back + reload: intact. Screenshots read: `race_a_round0` (A: "Your apply
wrote nothing -- the live chip changed in between", naming B's change and offering
Pull & apply), `race_b_round0` (B in sync, its value live), `race_a_final`.

Two earlier runs are part of the result. In the first, both tabs were on `127.0.0.1`,
whose cookies ignore the port, so both windows sent one `sm_actor` cookie and every
event named one person; the windows were moved to two hosts (`127.0.0.1`, `localhost`).
The next run (10 rounds) found the shared-working-copy record mismatch in its round 0
(described above): window A's event named A's edit while the chip took the content B
had saved. It was fixed and pinned before the final run. Only what was started was
stopped (the two servers and Chrome); the rig copy, the perf copies and the Chrome
profile were deleted (absolute paths).

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
5. **Two windows on one chip.** Their writes serialise on the chip lock and their lines
   on the journal lock (review round). The ledger order of their SM events is still
   projection order (`BEGIN IMMEDIATE`), and a line waits for its live writer for at
   most `WRITER_GRACE_S`. S5's ordering by `t_utc_us` should replace the append rank.
6. **The whole-chip difference of a saved or staged press** (~125-270 ms on an 18.5 MB
   chip) is on the request thread, outside the lock. Moving it off the request thread
   would need the line written before its entries are known, which the write-ahead
   contract forbids.
7. **An entry's `by` after Pull & apply.** The tray's Apply pulls live and re-stages
   the tray edits from the `pending_reapply` stash, which keeps values, not who staged
   them; the re-staged entries then carry the modifier's default actor (`human`). The
   event's `actor` (who pressed) is exact. Seen in the browser race's merged round.
8. **`external_observed`** is not built (optional, and not trivial: see above).
9. **The chip identity changing inside a write.** A run state that names another chip
   (`extras.chip_name`): the event is filed under the chip dir resolved *before* the
   write. A later chip-dir move does not carry `events.jsonl` with it.
10. **Units from before S4** have no `meta.hub`, so undoing them links nothing (honest).
   A staged-only Ctrl+Z (live walk OFF) followed by a manual Apply links through the same
   `jrn:` group rule as the `alr:` revert. That rule is pinned through the `alr:` route;
   the staged-only path has no route pin of its own yet.
11. **`HubStore()` loads every path id on open, and writes `meta` on open** (docs/270
   P3). The projector opens it once per run of the tail. That is fine for SM-only
   ledgers. Keep one connection per chip in the projector before S5 puts 100k-path run
   ledgers behind it.
12. **`REVERTS_TO_EARLIER` is byte-based** (docs/270 P3). The same content serialized
    differently is not flagged.
13. **`OVERLAPS_SM_WRITE`** (DESIGN 3.4: `run_start < sm_event.t < created_at`) needs
    runs in the same ledger, so it belongs with S5's ingestion. The SM side of it is
    ready: every SM event carries its own UTC instant.
14. **Two windows of one install share one working copy.** A save in one window can
    replace the working files another window just saved and is about to write; the
    record now follows the bytes, but the other window's own edit does not land (its
    press still answers success). Serialising each press's save + write across
    processes (the chip lock taken before the save) would close it; the pre-apply backup
    and the staleness gates sit in between today.
15. **Bit assignment.** S3 uses 1/2/4. S4 adds UNDONE 8, PARTLY_UNDONE 16 and
    DERIVED 32. `schema_version` stays 1, because the tables are additive. A reader
    that predates S4 would not understand the SM rows. There is none outside this
    branch.
