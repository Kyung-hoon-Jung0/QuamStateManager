# 284: Versions and State History read the change ledger

S9 of the state-tracking hub, on `feat/hub-versions-r2` (base `origin/main` `cbc17bf8`).
Design source: `D:\work\study\2026-10-03_state-tracking-hub\DESIGN.md` §3.6 (the "Versions panel /
State History" row: `timeline(kinds with state)`, `state_at`, `diff`) and §3.7 S9 ("Versions/State
History on `state_at`, with legacy dirs still readable"), read with [269](269_hub_rules.md),
[270](270_hub_ledger_builder.md), [271](271_every_sm_write_is_recorded.md),
[275](275_the_ledger_keeps_itself_current.md), [279](279_hub_read_side.md),
[282](282_value_history_on_the_hub.md) and [283](283_chip_status_and_param_history_on_the_hub.md).

**Result.** The Versions panel and State History list the chip's change ledger: every run (`run #N`
+ its node), every SM write (actor and door), every state SM saw change outside SM (`seen by SM ...
writer unknown`), plus the older Param History snapshots no event holds. Diff and Compare read the
ledger's merged document (`HubStore.state_at`); Stage and Restore-live hand the EXACT pair (the
event's own saved files verified against the raw hash the ledger read, or the pair SM wrote) to
their existing doors, gates and record unchanged. The golden check compared the ledger replay with
Param History's own copies on four byte copies (two run archives, two rig histories): **0
mismatches under the S2 rule in 264 matched snapshots**, the exact pair typed-equal to the
snapshot's two files in **264 / 264**, the replay of every event no snapshot shows equal to its own
saved files (**101 / 101**), and the list hides no state and lists none twice (0 / 0 on every
copy). Typed-JSON equality is NOT what the replay gives: **0 of 255** run / observed documents are
typed-equal (an int comes back as a float; on one rig a long float array comes back with ints),
which is why no write ever takes the replay. Round trip: Stage + Apply and Restore-live through the
old id and the new id wrote **byte-identical** live files on both archives, and each Revert last
apply gave the baseline bytes back. Every fault is said in its own words on six surfaces (9
faults); every refused press refuses before any confirm, backup or write. Warm on a 10,000-run
chip: the panel 14 ms (was 20), State History 20 ms (18), Diff 6.5 ms (2.4), Compare 12 ms (10),
Stage 38 ms (25); cold 0.03-1.1 s (§7). Pins: 29 test functions (42 cases), **72 / 72 mutations
RED, 29 / 29 pins turned RED**; 52 related test files 2,966 passed (one load-timing flake of an
untouched JS selfcheck, §9); the real-browser journey **ALL OK (48 checks, 0 console errors)**, which
found an endless refetch loop in the panel, fixed and pinned (§8).

## 1. Spec (binding rules)

### 1.1 What moves

| Surface | Before | After |
|---|---|---|
| Versions panel (`GET /state/versions`) | `list_snapshots` metas, a changes-only filter over `diff_summary`, the quick diff by `diff_snapshots` | `hub_versions.read`: the ledger's state-bearing events through `hub_query.timeline` + the older snapshots no event holds; the quick diff between the two newest readable rows of this folder |
| State History (`GET /state-history`) | snapshot folders, paged | the same rows, paged by the ledger total |
| Row Diff (`/state/versions/<id>/diff`, `/api/history/<id>/diff`) | `hm.diff_current` (snapshot store) | a ledger id: the merged document (`hub_versions.diff_view` / `document`) under `compare_equal`; a snapshot id: unchanged |
| Compare 2 (`/diff/snapshots` -> `/diff`) and 3+ (`/diff/versions`) | `json_diff.build` / `Differ.diff_n` over snapshot stores | a ledger id: the merged document; the State tab merges every side, the Wiring tab says why it is empty; equality `compare_equal`; columns in the timeline's order and named by their event |
| Stage (`/state-history/<id>/stage`), Restore-live (`.../restore-live`) | `hm.load_snapshot` | a ledger id: `hub_versions.exact_pair`, or a refusal with its reason before any confirm, backup or write; the door, its gates and its S4 record are the same code |

### 1.2 Two documents, never mixed

* **Diff and Compare** read `HubStore.state_at(eid)`: the ledger needs no archive file. It is
  display-equal (the S2 `same` rule, `compare_equal`), never write-exact: `changes.num` is a REAL
  column, so a stored int comes back as a float, and a scalar list longer than 16 is stored as one
  S2 array blob whose integral floats are canonicalised to ints. Measured in §4: every run and
  observed document differed from its file in type (6,196 to 102,911 int leaves per copy came back
  as floats; on one rig 50,560 float elements of long arrays came back as ints), never in value.
* **Stage and Restore-live** take `hub_versions.exact_pair`: a run or an observed state -> its own
  saved files, read on every press and accepted only when `rules.state_hash(bytes)` equals the
  ledger's raw `state_hash`; an SM write -> the anchor blob SM kept (checked against the content
  hash recorded at the door), or a replay from its base checked against `post_chash`. A merged
  document is never split back into two files by guess.
* Refusals, each with its reason: `DERIVED` (an SM write projected without its bytes), a missing
  ledger blob, `SOURCE_GONE` (the run's files deleted or rewritten), an uncertain chip identity, an
  id from an earlier build of the ledger, an unreadable ledger, and -- not a refusal but "not now" --
  a ledger still being built.

### 1.3 Version ids

`<UTC stamp of the event>_event-<eid>`, e.g. `20260101_120020_000000_event-2`. The stamp is checked
against the event: after a ledger rebuild an id names another event, and the id refuses ("from an
earlier build of the change history; reopen the list") instead of answering with another state. A
snapshot id (`YYYYmmdd_HHMMSS_...`) keeps its old meaning everywhere.

### 1.4 Rows, and older snapshots never drawn twice

Rows are events with a state: a run that saved one, an observed state, an SM write that landed.
Runs of an uncertain chip identity are not listed; a note counts them. An older Param History
snapshot is listed only when no event holds its state: the ledger's own observed-import verdict
for it, else its content hash equal to an event's (a run snapshot: only an event of THE SAME run
and node). Its content hash comes from a cache beside the snapshots (`_version_hashes.json`, a pure
function of write-once folders), filled by a worker; until a snapshot is matched its row says
"older snapshot (matching)" and a note says the list may repeat a state for a moment.

### 1.5 Modes (S7's words)

`building` / `preparing`: both surfaces draw the older snapshot path with "The change history is
being built (n of N runs)..." / "Preparing the change history..."; every door with a ledger id says
"... try again when it is complete". `fallback` (`no_ledger`, `no_runs`, `unreadable`): the old
path, labelled "Older snapshot history: ...". `ledger`: the rows, with S7's sync notes.

## 2. What was built

| File | What |
|---|---|
| `core/hub_versions.py` (new) | ids (`ref_of`, `parse_ref`), a read-only view of the ledger (`_Ledger`, `mode=ro`), `document` / `documents` / `diff_view` (the merged document, shared read-only, LRU 6), `exact_pair`, `availability` (why Diff / why Stage+Restore are not offered: two independent answers), `compare` / `compare_n` (`Differ` under `compare_equal`), the snapshot-hash cache + worker, `coverage`, `read` (rows of one page; the whole-ledger part cached on the read index's version and the snapshot list), `NotReady` / `building_text` |
| `web/routes.py` | `_versions_read`, `_version_rows` (the words), `_version_sm_words`, `_version_live_chash`, `_version_run_uid`, `_version_side` / `_version_order` / `_version_diff_now` / `_version_quick_entries`; the panel and State History branch on the mode; Stage / Restore-live / label / Diff / Compare / workbench accept a ledger id; `_history_chip_mismatch` (an id names its chip), `_version_pair_or_refusal`, `_history_noun` |
| `core/compare_sources.py` | `hist:<chip>/<ledger id>` resolves to the merged document (`wiring_missing`); "being built" is a transient error |
| `core/json_diff.py` | `build(..., equal=)` (additive) |
| templates | `_state_versions.html` (ledger rows, Stage, a disabled Diff with its reason, the reasons, no `data-changes` on ledger rows), `_ledger_state_history.html` (new), `_state_history_body.html` (branch + notes) |
| tests / tools | `tests/test_hub_versions.py`, `tests/browser/journeys/hub_versions.cjs`; `tools/check_hub_versions.py` (golden + round trip), `tools/faults_hub_versions.py`, `tools/perf_hub_versions.py`, `tools/mutate_hub_versions.py` |

## 3. Review of the drafts, and what this round changed

The first draft of this step (an automated draft) was reviewed and largely redone before this
round (recorded in the hand-over note): its default `state_at` read run files first (it changed
every S6b/S7 caller and made its own golden check circular), it read a `QuamStore` per snapshot on
every ledger change, it added an archive gate to Stage, blocked every label on a ledger chip, and
cached rendered HTML. This round finished the step; what it found and changed:

| Found | Why it was wrong | Now |
|---|---|---|
| `availability` returned one reason for both Diff and the writes | a missing blob of a run's shape hid Stage / Restore although its saved files are exact | two answers: Diff needs the blobs, a run's write needs its files, an SM write's needs its kept pair |
| a building ledger said "hub: still computing after 0.000s" at Diff, Compare and the workbench | a raw exception text | `NotReady` with S7's sentence; the workbench treats it as a transient error |
| an unreadable ledger answered "file is not a database" | raw | "The change history could not be read (...)" at every door |
| `exact_pair` remembered a run's pair in RAM | a run folder deleted after one press still staged; the panel said SOURCE_GONE | a run's files are read on every press; only an SM write's kept pair is remembered |
| every open stat'ed every snapshot twice and ran one query per run snapshot | 592 ms warm on a 2,000-snapshot chip | the whole-ledger part is cached on the read index's version token and the snapshot list; a known snapshot stamp is not stat'ed again (write-once folders); coverage is three queries |
| the cache held snapshot objects | a label given since was drawn as it was | the cache holds stamps; rows are this request's objects |
| Diff and Compare opened one ledger connection per lookup (up to 9 per Compare) | most of their warm time | one read snapshot per request (`documents`, `diff_view`); Pull to Live from the cheap check, the door re-checks |
| the ledger panel carried `data-changes="all"` | the browser re-asked for the panel whenever it differed from its own choice: **23 requests in 4 s** while the panel stood open, every tick lost (found in the real browser, §8) | no `data-changes` on ledger rows; pinned |
| an archive (a run's own files, read-only) marked a row "on this now" | the mark names the LIVE chip's content | only for a live chip |
| the Stage / Restore confirms called a version "this snapshot" | a version is not a snapshot folder | "this version" |
| while building, the panel said "No recorded versions for this chip yet" | it denied the runs being read | "No older snapshots to show meanwhile." under the building note |
| the tools: golden compared run files with themselves; numbers unverified; a person-like actor name | circular; not measured | rewritten (§4-§9), actor `operator` |

## 4. Golden comparison

`tools/check_hub_versions.py` on byte copies (`shutil.copy2`, never a link; every copy deleted after
its run). **Archive A**: the run archive copy already kept under the QA rigs for an earlier round (39
runs, copied again into scratch, the rig never written). **Archive B**: the newest 200 runs of a
second real archive, copied from the read-only source. Each archive: SM opens a chip made from the
newest run, builds the ledger, Param History backfills its own copy of every run (the OLD path), 10
outside edits are captured by `auto` snapshots (S7's injector), SM writes go through the real doors
(edit + Apply as `operator`, every third undone). **Rig history A / B**: the Param History, chip and
data folder of two QA rigs (40 and 647 snapshots), copied; SM imports the snapshots that are not
runs as observed states.

Each Param History snapshot is matched to the event holding its state by a rule independent of the
code under test (a run by its own snapshot key, a capture by the ledger's import verdict, anything
else by content hash, nearest in time); then the **ledger replay** (`hub_versions.document`) is
compared with the snapshot's merged document, and `exact_pair` with its two files.

| | Archive A | Archive B | Rig history A | Rig history B |
|---|---:|---:|---:|---:|
| runs copied (with a saved pair) | 39 (39) | 200 (191) | 3 | 39 |
| of another chip identity (not listed) | 20 | 0 | 0 | 20 |
| ledger events (run / observed / SM) | 48 (39 / 5 / 4) | 209 (191 / 10 / 8) | 42 (3 / 39 / 0) | 108 (39 / 69 / 0) |
| Param History snapshots | 20 | 134 | 40 | 647 |
| matched to an event | 19 | 133 | 40 | 72 |
| **replay vs snapshot: typed-JSON equal** | 3 (the SM writes) | 6 (the SM writes) | 0 | 0 |
| **replay vs snapshot: equal under S2 `same`, type differs** | 16 | 127 | 40 | 72 |
| **replay vs snapshot: MISMATCH** | **0** | **0** | **0** | **0** |
| type-only leaves over all comparisons (int saved, float replayed) | 6,388 | 107,735 | 110,353 | 23,582 |
| type-only leaves (float saved, int replayed; long-array elements) | 0 | 0 | 0 | 50,560 |
| **exact pair vs the snapshot's two files: typed equal** | **19 / 19** | **133 / 133** | **40 / 40** | **72 / 72** |
| events no snapshot shows | 9 | 76 | 2 | 17 |
| -- their replay vs their own saved files (S2) | 8 / 8 | 74 / 74 | 2 / 2 | 17 / 17 |
| snapshots no event holds (listed as older) | 1 | 1 | 0 | 575 |
| **listed although held / held-not-listed** | **0 / 0** | **0 / 0** | **0 / 0** | **0 / 0** |

Every unmatched item is classified (none "unexplained"):

* **Snapshots no event holds.** Archives A and B: the backup Param History took right before the
  first SM write -- the live chip held the newest run's state plus the data-folder link the setup
  added, a state no run saved and no capture had imported yet; it is listed as an older snapshot.
  Rig B: 572 run snapshots of runs not in the copied data folder (this rig's history holds runs of
  two archives and of runs beyond the copy), and 3 run snapshots whose run's saved files differ from
  the snapshot copy now (the same run folder; the copy holds a top-level key the files no longer
  hold -- the ledger holds the files, the snapshot holds its own state, both are listed).
* **Events no snapshot shows.** Runs whose state a snapshot of another moment holds (Param History
  keeps one copy per content), runs Param History took no snapshot of, undo writes (Param History
  keeps no copy of them), and on rig B the 3 runs above.

## 5. Round trip: the old id and the new id through the same doors

On each archive copy, one run version that has both ids (its Param History snapshot and its ledger
version) and differs from live: Stage + Apply, then Revert last apply; Restore-live, then Revert
last apply -- first through the old id, then through the new id.

| | Archive A (run #36) | Archive B (run #2650) |
|---|---|---|
| Stage + Apply: live bytes old id vs new id | **identical** (97,512 + 2,532 B) | **identical** (314,636 + 7,193 B) |
| Restore-live: live bytes old id vs new id | **identical** | **identical** |
| live after each = the run's own files (typed) | yes | yes |
| each Revert last apply -> baseline bytes | yes (4 / 4) | yes (4 / 4) |
| Restore backed the live chip up first | yes (both ids) | yes (both ids) |
| S4 records | `sm_apply` / `apply_staged`, `restore` / `restore_live`, `sm_apply` / `revert_last_apply`, all `human:operator` | the same |

## 6. Fault table (the real routes)

`tools/faults_hub_versions.py`, synthetic chips; the real mechanism wherever one exists. "Refused"
means HTTP 404 with the reason, and the live files, the snapshot folders, the SM journal and the
working state unchanged (checked for every refused press, with an unsaved edit pending in the pins).

| Fault (how) | Versions panel / State History | Diff, Compare | Stage, Restore-live |
|---|---|---|---|
| missing blob: a run's shape blob deleted (shared by 3 events) | row listed, Diff disabled, "Missing ledger blob: the change history cannot rebuild this state."; Stage / Restore offered | "Missing ledger blob: the change history cannot rebuild this state (...)" | accepted: the run's saved files are exact (verified hash) |
| missing blob: an SM write's kept pair deleted | row: "Missing ledger blob: the files this SM write produced are not kept, so it cannot be rebuilt."; no Diff / Stage / Restore | refused, same words | refused, same words |
| SOURCE_GONE: run folder deleted, sync sweep (flag 256) | row: "SOURCE_GONE: this run's saved files are gone. Diff and Compare read the ledger's merged document; Stage and Restore need the two original files."; Diff offered | answered from the ledger (3 values) | refused "SOURCE_GONE ..." |
| uncertain chip: a run saved under another chip name (flag 4) | not listed; "1 run of an uncertain chip identity is not listed." | refused "This run's chip identity is uncertain ..." | refused, same |
| stale id: ledger deleted, an older run added, rebuilt by the sync | the old id not listed, the new id listed | refused "This version id comes from an earlier build of the change history; reopen the list." | refused, same |
| DERIVED: an SM write landed with no post bytes (the restart path) | row: "DERIVED: the ledger derived this SM write's state from its entries; no exact state was recorded."; no Diff / Stage / Restore | refused "DERIVED ..." | refused "DERIVED ... no exact copy of its files exists to stage or restore." |
| unreadable ledger: `ledger.sqlite` overwritten | older snapshot path, "Older snapshot history: the change ledger could not be read, ..." | refused "The change history could not be read (file is not a database)." | refused, same |
| building (status seam, 1 of 3) | older snapshot path, "The change history is being built (1 of 3 runs). Until it is complete, ..." | "The change history is being built (1 of 3 runs); try again when it is complete." | the same sentence (not "cannot", "not now") |
| preparing: another read holds the chip's read connection | older snapshot path, "Preparing the change history..."; the ledger rows once it is released | answered (Diff reads its own snapshot of the ledger) | -- |

Each row is a pin (`test_every_door_refuses_a_fault_with_its_reason_and_writes_nothing`,
`test_a_runs_missing_blob_stops_its_diff_not_its_exact_files`,
`test_a_version_the_ledger_cannot_hand_over_refuses_before_any_write`, `test_modes_other_than_ledger_say_why`).

One limit, by design: the list's checks are cheap (SQL and two stats per row). An SM write that
replays from a kept pair that is missing is still offered Diff (and Stage / Restore on State
History); the press refuses with the reason (measured: "Missing ledger blob ...").

## 7. Performance

`tools/perf_hub_versions.py`: server time through the Flask test client, one process, the same
chip for both sides. "Before" is the old body exactly (`routes._versions_read` stubbed to "no
ledger", Diff / Compare / Stage given the Param History snapshot ids of the same runs); "after" is
the ledger with ledger ids. Cold: each side's first request after every RAM cache of both paths was
dropped (the persisted snapshot-hash cache stays, as across a restart). Warm: median of 9 passes,
the sides alternating. Synthetic chip: 10,000 runs, 32 qubits, one T1 moved per run; Param History
backfilled with all 10,000 (365 s); ledger built on open (52 s). The PC was running two test-suite
shards; numbers move by tens of percent between runs.

| Surface (ms) | cold before | cold after | warm before | warm after | p90 before / after | bytes before -> after |
|---|---:|---:|---:|---:|---:|---|
| Versions panel | 548 | 944 | 19.7 | **14.4** | 27.4 / 15.6 | 106 -> 130 KB |
| State History (htmx) | 1,005 | 1,114 | 18.0 | 20.0 | 21.2 / 23.7 | 113 -> 96 KB |
| Diff vs now | 252 | 490 | 2.4 | 6.5 | 5.2 / 9.4 | 11 KB |
| Compare (3) | 239 | 230 | 9.6 | 12.4 | 15.6 / 16.9 | 85 KB |
| Stage | 83 | 30 | 25.1 | 38.0 | 29.3 / 43.0 | -- |

* **First open with no hash cache at all** (10,000 snapshots never matched): the panel answers in
  1.9 s with the older snapshots marked "(matching)"; the worker matches all of them in 25 s; the
  next open is 162 ms and says nothing pending.
* The first version of this round measured warm 47 / 40 / 10 / 37 / 36 ms (panel / State History /
  Diff / Compare / Stage) and 592 ms for the panel on a 2,000-snapshot chip; the fixes in §3 (the
  cached summary, one ledger snapshot per request, the memoised run link, two plain stats per row)
  brought it to the table.
* Warm Diff (+4 ms) and Stage (+13 ms) pay for honesty: Diff opens a read snapshot to check the id
  against its event; Stage reads and hashes the run's two files on every press. Cold opens include
  S6a's RAM read index build for the 10,000-event ledger.

## 8. Real browser

SM served from this worktree on **5165** (waitress, the conda env this machine uses for real-browser runs), sandboxed `HOME` /
`USERPROFILE` / `APPDATA`, no qualibrate config, against a **copy** of a QA rig: 39 runs of a real
archive (20 of another chip identity) and a live chip made from the newest run (its data folder
linked to the copy, its network pointed at `127.0.0.1:1`). Headless Chrome on CDP **9485**, a
scratch profile, real mouse events with the button mask; journey
`tests/browser/journeys/hub_versions.cjs`: **ALL OK, 48 checks, console errors 0** (two 409 replies
of refusal presses, which the browser logs as failed loads, counted apart; no CSP message appeared;
two dialogs answered OK: Restore's confirm, and a leave-page prompt while an edit was pending).
After the walk the live files were byte-identical to the baseline. Screenshots read one by one:

| Step | Seen |
|---|---|
| Versions while the ledger was still being built | "The change history is being built (1 of 39 runs). Until it is complete, this list shows the older snapshot history." + "No older snapshots to show meanwhile." (`00_versions_while_building.png`) |
| Versions | "20 runs of an uncertain chip identity are not listed.", rows "run #41 06_... saved at the run's end" with Diff / Stage / Pull to Live, the quick diff "#2 -> #1 0 changes" (`01_versions.png`) |
| Diff a version vs now | "Version 2026-09-07 16:39:39 -> now", Modified 15: the data-folder link, the network, and `core` null -> `q1_core` (`02_diff_vs_now.png`) |
| Compare 3 | "Versions compared ... 12 differing keys", columns "EXP run #38 / #39 / #40" + their ids, oldest left (`03_compare_three.png`) |
| Stage run #40 | top bar "Staged version - not on live", toast "Version ... loaded as the working state" (`04_staged.png`) |
| Review tray | "Staged version - not on live", 3 fields, Apply / Take live / Keep mine (`05_review_tray.png`) |
| Applied | toast "Applied to the live chip."; the page as rendered when Apply was pressed, before the write was projected (runs only) (`06_applied.png`) |
| Reverted | the page as last rendered between the two writes: "applied by a person SM write (apply_staged) on this now", and the pre-apply backup (pinned "Live-tracking baseline") as an older snapshot until the sync imports it as a state SM saw (`07_reverted.png`) |
| State History | "applied by a person SM write (revert_last_apply) on this now", "... (apply_staged)", runs (`08_state_history.png`) |
| Restore-live with an unsaved edit | the confirm asked, then "You have unsaved edits in the working state. Restoring this version to live will discard them." (`09_restore_refused_unsaved.png`) |
| An archive (run #40's own files, read-only) | Versions rows with Diff only, no Stage / Restore, no "on this now"; a direct Restore-live press: 409 "This chip was opened from a dataset run archive (read-only)." (`10_archive_versions.png`) |
| Compare from State History, Back, Forward, reload | Back: State History whole, one top bar; Forward: the table; reload of the table URL: whole page with chrome (`11_after_back_forward_reload.png`) |

Found and fixed on the way: the endless refetch loop (§3); the harness's mouse events carried no
button mask, so a checkbox never toggled (the journey's own clicks now carry it).

Found and left alone (not S9 code, unchanged since the base): Back onto the Agent home (`/`) after a
Versions Compare nests a second app shell in the pane -- `home()` answers an htmx re-sync with the
full page (the journey starts on State History, whose route answers with its pane); after a Stage,
the live-drift card on State History says "3 parameters changed on the live chip" although only the
working state changed (`_live_drift.html`); and the State History body is not fetched again after
an Apply from the review tray, so its "on this now" mark can be one write old until the page is
reopened (`07_reverted.png`; its refresh rule is unchanged).

## 9. Pins, mutations, tests

`tests/test_hub_versions.py`: **29 test functions (42 cases)** on synthetic chips through the real
routes; no customer data; no existing test edited.

`tools/mutate_hub_versions.py`: one source edit at a time (exactly one occurrence of its anchor, in
the file's own line ending), the pins it names run, the file restored from the bytes held in memory
and read back (never `git checkout`); RED only on a failed assertion in a targeted pin's own
traceback. **72 / 72 RED; 29 / 29 test functions turned RED.** The first sweep of this round (66
mutations) left one survivor and one never-RED pin: the State History page's own ledger branch
(the template's branch alone hid the route falling back -- now pinned by paging), and the typed
exact pair (no mutation reached it -- now one reads saved ints as floats). The final sweep left one
survivor, a vacuous pin: the relabelled-snapshot check reused the cached objects themselves, so a
cache of stale rows passed; it now builds fresh objects, as a new listing does (the five mutations
on that pin re-run: RED). Results: [284_versions_mutations.json](284_versions_mutations.json).

**Existing tests** (the test env, `--timeout=900`, three files per process, serially, while two
full-suite shards of another branch ran on the PC): every test file that reaches the Versions
panel, State History, its stage / restore / label doors, the row Diff, `/diff` and `/diff/versions`,
`compare_sources`, `json_diff`, history, sync and undo, plus every `tests/test_hub_*.py` and
`test_web.py` -- **52 files: 2,966 passed, 38 skipped, 1 failed.** The one failure,
`test_hub_drawer.py::test_the_retry_selfcheck`, is a jsdom selfcheck of the value drawer's retry
(static JS only, byte-identical to the base): it saw one extra background poll request inside its
window under load, and passed 3 / 3 alone and in its batch re-run (78 passed). After the last edits
(comments only, and the strengthened pin): `test_hub_versions.py` 42 passed.

## 10. What S10 can delete, and when

Same condition as S7 / S8 (docs/282 §9, docs/283 §10): after the user's full-day verification
**and** once no chip reaches the fallback (every chip's runs reach its ledger).

Deletable then (S9's surfaces no longer read them):

| Code | Note |
|---|---|
| the snapshot branch of `state_versions_panel` (the changes-only filter, `hidden_unchanged`, the snapshot quick diff) and its `data-changes` / `StateVersions.setChanges` client side | the old list |
| the snapshot branch of `state_history` (`_paginate(snapshots)`, the zero-diff baseline label, `disk_stats`) and the snapshot loop of `_state_history_body.html` | the old page |
| `diff_versions`' `Differ().diff_n(stores)` branch | `hub_versions.compare_n` serves snapshots too (one rule) |
| the `fallback` / `building` labels of both surfaces | with the fallback |

**Not deletable by this step alone:**

* Param History snapshot folders and `load_snapshot` / `diff_current` for snapshot ids: older
  snapshots stay readable (listed, Diff, Stage, Restore), and **Revert last apply stages the
  pre-apply backup snapshot**.
* Full-copy capture of runs: Stage / Restore of a run reads the run's own folder; a run folder
  deleted later (SOURCE_GONE) can no longer be restored exactly from the ledger, while its Param
  History copy could. Deleting the capture trades that away -- a user decision. Other readers of
  run snapshots: `chip_trends_ram` and the chip report's Trends, `extract_property_history`
  (`/api/topology/sparklines`, Auto Calibrate's drift gate via `column_history`), `leaf_index`
  (docs/283 §10).
* `_spawn_post_apply_snapshot` and its throttles: the ledger keeps an SM write's exact pair (anchor
  or verified replay; typed-equal in §4), but the version chip (`_state_version_now`), Auto-Sync and
  the pre-apply backup still read snapshots -- check each consumer first.
* `_version_hashes.json` and its worker: needed as long as older snapshots are listed.

## 11. Residual limits

* **Numbers in Diff / Compare come back as floats.** `changes.num` is REAL: an int shows as `1.0`
  where the file has `1` (seen in the browser's Compare: a port `1.0`). Equal under the one rule,
  never written (Stage / Restore take the files). Keeping the type needs a ledger column (an S3
  change), not an S9 one.
* **The list's checks are cheap**; a press re-checks exactly (§6).
* **First open of a chip with many never-matched snapshots** answers with "(matching)" rows until
  the worker is done (25 s for 10,000).
* Ledger ids name one build of the ledger: a bookmarked id refuses after a rebuild, by design.
