# 281: Calibration log on the chip ledger

Hub step S6b on `feat/calibration-log-hub`, based on `integ/batch11`
(origin/main `1ef8983c` + hub S4/S5 + the read side). Design sections 3.1,
3.6 and 3.7 were read with [263](263_project_time_zone.md),
[269](269_hub_rules.md), [270](270_hub_ledger_builder.md),
[271](271_every_sm_write_is_recorded.md),
[275](275_the_ledger_keeps_itself_current.md),
[276](276_calibration_log_search.md), [279](279_hub_read_side.md) and the S6
seam in [277](277_chip_report_v2.md) section 7.

A first implementation was written by another agent and left uncommitted when
it ran out of credits. This round reviewed it, kept what was right, fixed what
was wrong (section 2), re-ran the S6 check with a stronger baseline (section 3),
and finished the pins, the mutation check, the regression and the real-browser
journeys.

## 1. What the page reads now

`story.build_day(..., ledger=<project-bound context>)` reads one day through
`hub_query.timeline(day_from=day, day_to=day)`. The day of an event is the date
of its instant in the PROJECT time zone (docs/263); the clock shown on a card
is that instant in that zone.

| Card | Source | What it shows |
|---|---|---|
| Run | a `run` event | gate verdict, figures, attached journal lines, claims/notes, author (section 10, P0-1/P0-2: a claim on this card, else the agent record that names this run's folder and number -- certain -- or a finished attempt matched by node and time -- inferred), the ledger's flags as badges with one line each; its rows are the event's exact change rows (the run's saved state vs the ledger state just before it), each with its op |
| First state | the ledger's first event that has a state | "First state in the chip history: N values recorded; there is no earlier state to compare against." The rows are not listed |
| SM write | an event whose kind is in `hub_store.SM_KINDS` and whose outcome is `landed` | a plain name ("Applied to the chip", "Undo (Ctrl+Z)", "Kept mine, overwrote live"), who, every entry SM recorded at the door with each entry's own actor; "undone" / "partly undone" from the ledger flags, a partly undone write marking the rows taken back; an undo or redo names the write it takes back by name and time, linked to that card |
| Agent run without a folder (C-17) | `agent_runs/index.jsonl` merged by key with each Registry `meta.json` (read without constructing the Registry) | "<agent> ran <node> on <targets>. No run folder: <reason in words>.", the recorded error, its plan and step, the stated purpose separately; laid on the run row's columns. A step that never ran says "did not run" and is not counted as a run |

A save of the working copy is not a write to the chip and never appears as
one. Changes made outside SM appear inside the next run's rows; they are not a
separate card.

Every before -> after on the page -- run rows, SM-write entries, the run's
parameter changes, the day's largest delta -- and in the report's log section
goes through ONE macro, `before_after` in `_delta_macros.html` (docs/76): values
through `groupdigits`, the difference through `value_delta` (`delta_chip`). The
row's op decides the words, so a side that did not exist is never printed as
`None`:

| op | renders |
|---|---|
| `add` | `+ added <new>` |
| `gone` | `- removed was <old>` |
| `set` | `<old> -> <new>` and the delta chip when both are numbers |
| `retarget` | `retargeted <old pointer> -> <new pointer>` |
| `first` (legacy snapshot feed only) | `<new> first recorded` |

A JSON null prints `null`. A scalar list longer than 16 is one ledger value,
`{"_array": n, "_hash": h}` (docs/269); it prints `[n values]` with the hash in
the tooltip, and `contents changed` when both sides are arrays. The page's
card and the report share the row markup through `_journal_changes.html`.

Honesty states, each pinned:

* **building** (`hub_sync` building, or `Warming`): "building the history
  (n/N)", no cards and no day totals; independent journal lines stay readable
  and adoptable under "Recorded journal lines only".
* **degraded**: the day renders, its totals are labelled incomplete, and every
  unreadable root, failed run folder and error is named.
* **unavailable**: no project zone, or no history dir for the open chip, or no
  chip history built by this window -- one line says so and the page does not
  poll (the missing dir used to be a 500).
* **idle**: a ledger that this window is not keeping current is labelled "As
  last recorded" (totals included); it is never presented as the whole day.
* **one SM write whose recorded entries cannot be read**: that card says so and
  shows the ledger's own rows for the write; the rest of the day is unaffected.
* **SM edits from before the ledger recorded SM writes**: the undo journal is
  not history (it holds working-copy saves and never says whether one reached
  the chip), so such edits are not cards. A day that has them says how many,
  and from when the chip history records SM writes. Units a recorded write
  names (its journal stamp can precede the event by a few ms) and units after
  recording began are never counted.

Search (docs/276) keeps its grammar: the server builds one haystack per card
and line from what the card SHOWS (never instants, ledger ids or folder
hashes; "undone" and "partly undone" are searchable), the page filters as you
type, every group and count follows the filter, folderless agent runs count as
runs. A cancelled agent run is not counted as failed.

A day of more than 150 cards sends each card's row (with its whole search
text) and fetches a card's body when it is opened (`GET /journal/card`); a
person's page checks the gates in the background and refreshes the day once
they are checked, never under an open card (section 10, P1-8).

The report section `calibration_log` is built by
`routes._report_build_calibration_log`: every day the ledger, the agent
records and the journal know (one `index.postings["day"]` read plus the page's
own agent-record reader), each through `journal_routes._build` -- the page's
day builder, gates checked in the request, never lazy -- and rendered with the
page's row template. Redaction runs on its output like every section, widened
for a log (section 10, P0-3); its HTML bypasses the chip-state HTML cache (the
ledger and agent records move independently of the state) and it does not
hold the report's shared build lock. Lines of a session that named no chip are
left out. While the ledger builds, the section says so and that it is
incomplete.

## 2. Review of the uncommitted implementation: what changed and why

| # | Found | Changed to | Pin |
|---|---|---|---|
| 1 | A key a run or write ADDED printed `None -> target`; a removed key `3 -> None` (coordinator finding) | the row's op decides the words (`before_after`) | `test_run_card_rows_say_added_removed_...`, `test_sm_write_rows_say_added_removed_...` |
| 2 | Values printed raw (`-7.995537540056441e-05`, `5169000000.0`) (coordinator finding) | `groupdigits` + `value_delta` chip through the shared macro, on run cards, write cards, parameters, the largest-delta pill and the report | same two + `test_report_log_rows_use_the_page_template`, `test_run_parameters_read_through_the_same_before_after` |
| 3 | The ledger's first event listed every leaf of the chip as "added" (13,086 rows on the rig chip; most of a 3.7 MB day fragment) | one "First state in the chip history" line with the count | `test_first_ledger_event_is_a_starting_state_not_added_values`, `test_a_large_first_state_count_reads_grouped` |
| 4 | Long arrays printed as `{"_array": 20, "_hash": "..."}` | `[20 values]`, `contents changed` | `test_a_long_array_row_reads_as_its_length_not_its_hash` |
| 5 | An undo card named a bare 16-hex journal id | "Undoes the apply write of 05:00:19", linked to that card (in-file anchor in the report) | `test_undo_marking_and_named_target` |
| 6 | One unreadable entries blob withheld the WHOLE day as "unavailable" | per-card note, ledger rows for that write, the rest of the day intact | `test_missing_exact_entry_blob_is_said_on_that_card` |
| 7 | An agent record WITH a run id whose folder the ledger lacks said "no run folder" | "run folder #N is not in the chip history" | `test_agent_run_with_a_recorded_folder_missing_from_the_history` |
| 8 | The agent's stated purpose was printed as its failure reason when no error was recorded | reason = error / failure only ("reason not recorded"); purpose on its own line | same |
| 9 | A cancelled agent run counted as "failed"; the folderless card squeezed its sentence into the node column (truncated mid-word) | outcome `cancelled`; the card uses the run row's columns (node, targets, class badge, "-" for the run, who/plan), the full sentence in its body | `test_agent_run_row_scans_like_a_run_and_a_stopped_run_is_not_a_failure` |
| 10 | No history dir for the open chip raised `RuntimeError` -> HTTP 500 | an "unavailable" line, journal lines still shown | `test_a_chip_without_a_history_dir_says_so` |
| 11 | Every run event of the ledger was read on every render (only needed to match agent records) | read only when agent records exist | `test_ledger_rows_of_every_run_are_read_only_for_agent_records` |
| 12 | Large SM entries re-parsed the whole primary journal once per event | the journal's lines read once per query | (performance; `test_large_sm_entries_load_from_primary_blob` keeps the behavior) |
| 13 | Same-instant cards ordered by card kind, not by the ledger | ties broken by the ledger's canonical order | `test_missing_exact_entry_blob_is_said_on_that_card` |
| 14 | The write-card filter listed door names as event kinds | `hub_store.SM_KINDS`, outcome `landed` only | `test_only_a_landed_sm_write_is_a_write_card`, `test_save_is_not_a_chip_write` |
| 15 | The report builder carried its own copy of the agent-record reader; a building ledger silently dropped run-only days | `story._agent_records` (the page's reader); a building ledger is said | `test_report_lists_an_agent_only_day_and_says_when_history_is_building` |
| 16 | The day-fragment cache token listed 14 keys by hand (a new key would serve stale HTML) | every key the template reads | `test_day_fragment_refreshes_when_only_the_note_changes` |
| 17 | Pre-ledger SM edits vanished with no trace (found by the S6 check, part B) | the day note in section 1 | `test_pre_ledger_undo_edits_are_counted_never_shown_as_writes` |

Kept as written: the ledger read through a project-bound context, the
folderless matching (run id, else node + 900 s, over the whole ledger), the
building/degraded templates, the journal-line attachment across the
server-calendar/project-day seam, the bounded gate cache, the report
registration, the WAL-startup retry in `hub_store._enable_wal` (a real race the
earlier regression pass hit: `PRAGMA journal_mode=WAL` returning `database is
locked` while another window switches the mode), and the step recorded on agent
records.

## 3. The S6 check (DESIGN 3.7)

Measured on `61ad757c`. The review round (section 10) changed authorship
(P0-1/P0-2), flags and labels; it was checked by its own pins and the browser
journey, and this check was not re-run.

`tools/check_calibration_log_hub.py <config.json>` (all locations in an
untracked config; copies are ordinary files, never links; sources are only
read). The base is a detached worktree of `integ/batch11`
(`git worktree add --detach`), never a stash or checkout.

**Part A -- a copied run archive.** Three days, the first 30 runs of each by
run number (90 runs, 166,208,096 bytes), copied from a real lab archive. The
earlier check compared against a base log with NO Param History, so every
"old" card had no writes and every difference was trivially "gains writes". This
round gives the base what a running app has: every copied run ingested into
Param History in instant order through `HistoryManager.ingest_run` (the
near-real-time door; 70 runs got a snapshot, 20 were content duplicates). The
base log's "what it wrote" then comes from the snapshot change points, which is
what users saw. All 90 run cards were compared field by field (node, targets,
outcome, status, gate, author, because, journal, figures, note) and row by row.

| Run-card class | Count |
|---|---:|
| Rows identical | 54 |
| No change on either side | 16 |
| Rows differ only by the expected row classes below | 15 |
| Gains exact rows: the run had no Param History snapshot (its state repeated an earlier one) | 4 |
| First state in the history (the ledger's first event) | 1 |
| Any other field or row difference | **0** |

| Row class (within the 15) | Rows | Why |
|---|---:|---|
| Equal | 953 | |
| Snapshot feed capped at 200 rows per run | 370 | the old feed's `rows_per_snap=200` |
| Ledger-only key added or removed | 153 | the old feed indexed changes of existing numbers only |
| Ledger-only non-numeric leaf | 128 | the old feed never indexes strings or booleans |
| Snapshot feed followed a pointer | 63 | the ledger records the holder (docs/279) |
| Snapshot feed compared against an earlier run | 19 | the run before this one repeated an EARLIER state, so Param History deduplicated it and the old feed diffed against the snapshot before it; verified per row against that snapshot's state (runs after 19, 162-163 and 181; the ledger flags 19, 162 and 181 `REVERTS_TO_EARLIER`). The ledger is run by run, as decided |

Undone markings and saves: none in an archive (it has no SM journal); part B
covers them.

**Part B -- a copied SM instance** (the QA rig chip: chip, data folder and the
SM instance that ran the agent campaign; network 127.0.0.1:1). Both revisions
render every day either could show (data, journal and agent-record days, their
neighbours, today) through the page's own `_build`.

| Class | Count |
|---|---:|
| Agent runs without a run folder, now cards (C-17) | 5 |
| SM's own agent runs now attributed (the base looked records up by display name; they are keyed by the working-copy key) | 3 |
| Pre-ledger undo-journal edits: no longer write cards, counted in the day note | 5 |
| Run first state / gains exact rows / no change | 1 / 1 / 2 |
| Undone writes marked, SM write cards | 0 / 0 (this instance predates S4's journal) |
| Unexplained | **0** |

Of the five pre-ledger edits, the next run's saved state holds the value of
one (a forced overwrite), does not hold the other's two values, and three have
no later run; the live chip holds the values of three. This is why they are
counted, never shown as writes: the undo journal cannot say which reached the
chip. Undo marking and the undo card were exercised for real in the browser
(section 8): apply, apply, Ctrl+Z through SM's own doors.

## 4. Report pins: old expectation -> new, and why

By coordinator decision, exactly these assertions in
`tests/test_chip_report_v2.py` change (docs/277 section 7 scheduled the
"available once the ledger lands" state to end at S6); every other assertion in
that file is unchanged, including `DEFAULT_KEYS`.

| Test | Old expectation | New expectation | Why |
|---|---|---|---|
| `test_the_sections_and_the_calibration_log_seam` | unavailable, note "available once the ledger lands", not in `AVAILABLE_KEYS`, no builder | available, no note, in `AVAILABLE_KEYS`, builder is `_report_build_calibration_log` | S6 fills the seam (task item 6) |
| `test_parse_sections` | a requested `calibration_log` is dropped | kept, in declared order (`overview, calibration_log, raw`) | an available section is selectable |
| `test_the_panel_has_one_box_per_section_with_its_description` | checkbox disabled, pending note shown | enabled, no note, unchecked by default | default OFF: it spans every recorded day, so like the raw tree it can make a large file; keeps `DEFAULT_KEYS` unchanged |
| `test_an_unknown_or_unavailable_section_is_refused` | section route 404 "available once the ledger lands" | 200 with the section, built through `journal_routes._build` (spied) | one code path with the page |
| `test_an_unavailable_section_cannot_be_declared` | declaring it is refused as "not an available section" | a declared-but-missing section is refused; checked and present is accepted; unchecked leaves it out entirely | the confidentiality gates now apply to it as to every section |

Redaction of this section is pinned in `tests/test_calibration_log_hub.py`: a
host and an absolute path planted in a journal answer are present with the
switch off, hidden with it on, and stay hidden in the finalized file.

## 5. Measured

Server time of `GET /journal/day` (build + search haystacks + render) through
the Flask test client on this PC under unrelated load. Cold = first request
(gates computed); warm = five repeats, as served (the rendered fragment may be
reused) and with the fragment cache emptied before each (full build + render).

| Data | Busiest day | Cold (ms) | Warm as served, median (range) | Warm, fragment cache emptied, median (range) | Fragment |
|---|---:|---:|---:|---:|---:|
| Archive copy (part A) | 30 runs | 791 | 62 (61-80) | 88 (77-93) | 308,703 B |
| Synthetic ledger, 10,000 runs | 500 runs | 1,046 | 129 (117-141) | 206 (200-243) | 1,779,803 B |

Every warm request is under the 300 ms target, with or without the fragment
cache; cold renders are not (gates are computed on first sight and cached by
event state). Measured while the 213-file regression ran on the same PC.

The first-state change alone shrank the archive copy's busiest-day fragment
from 3,773,774 bytes (the earlier implementation's measurement) to 308,703 bytes.

## 6. Pins and the mutation check

`tests/test_calibration_log_hub.py`: 38 test functions, 56 cases with
parameters (synthetic ledgers, no lab data), plus the jsdom selfcheck `tests/calibration_log_hub_selfcheck.cjs`
driven by `test_building_journal_swap_selfcheck` (real route HTML; the swapped
building body stays searchable, adoptable and does not move the
since-last-visit stamp).

85 source mutations, each breaking one claimed behavior in shipped source (the
earlier implementation's 50, re-targeted to the reviewed source, and 35 for this
round's changes), each run against the pins that claim it and then restored
byte for byte with its timestamps: **85/85 RED** -- 83 by an assertion, 2 by an
exception (the two that turn an honest message back into the old failure: a
missing history dir and an unreadable entries blob raised past the page; the
test client re-raises them). Each of the 38 pin functions is claimed by at least
one mutation; two mutations also run the edited report pins in
`tests/test_chip_report_v2.py`. The harness is restartable by index and checks
after the run that every mutation target is intact.

## 7. Regression

The selection: every test file naming `journal`, `story`, `calibration.log`,
`chip_report` or `report_redact`, every `tests/test_hub_*.py` (159 files, the
same selection as the earlier pass), plus every file that reads the shared
pieces this round touched (`style.css`, `_delta_macros.html`, `value_delta`,
`hub_query`): 213 files. Run serially, one pytest process per file, in the
test environment with `PYTHONUTF8=1 --timeout=900` (jsdom installed, so the
selfchecks ran): **6,217 passed, 32 skipped, 2 failed** in 2,891 s.

Both failures are `tests/test_one_run_instant.py::TestRekeyMigration`
(`test_revert_restores_every_byte`, `test_the_journal_lands_before_the_first_move`),
the Param History re-key migration, which this branch does not touch. The class
is a known flake (docs/270 records one of its tests failing once): on the base
worktree the same file failed in 3 of 14 isolated runs, on this branch in 3 of
8, always inside that class, and every other run passed. Existing tests from
origin/main are unedited except the five report pins of section 4.

## 8. Real browser

SM served from this worktree on 5155 (waitress, one server at a time), headless
Chrome on CDP 9475 with an own profile, `tests/browser/journeys/cdp.cjs` (real
mouse events). Rig: an independent copy of the QA rig chip, its data folder and
its SM instance (`USERPROFILE`/`HOME` sandboxed, network 127.0.0.1:1, records
re-keyed to the copy's path, project zone set as the landing page would).

* **Journey 1 (rig copy).** Through SM's own doors: edit-batch (a frequency
  +1,000 Hz, a T1, a created key) -> Apply; edit-batch (delete that key) ->
  Apply; Ctrl+Z (`/undo`). Then `/journal`: three write cards per pass, the
  second marked UNDONE, the undo card "Undoes the apply write of 05:00:19"
  whose link lands on that card; `+ added 1.5`, `- removed was 1.5`,
  `5,078,710,199.903097 -> 5,078,711,199.903097 +1,000 (+1.97e-05%)`; no note on
  today (every unit belongs to a recorded write). Walked three days back with
  the previous-day button (10-04: the pre-ledger note, 1 edit; 10-03: 3 runs, 5
  agent runs without folders, the note, 4 edits; 10-02: empty), forward again,
  opened an agent card, typed `ramsey` letter by letter (3 cards, "Runs 3"),
  cleared it, opened run #2 (resonator frequency rows with deltas), followed its
  run link to the dataset page, Back (log intact on 10-03), reload (intact),
  and the first-state card on 09-30 ("13,086 values recorded"). Console
  errors: 0. Run four times as the code changed; the last run is on the
  committed code.
* **Journey 2 (archive copy, own instance).** 90 runs ingested (`ready 90/90`);
  on 10-02 and 10-01 a run card whose rows add keys and one with numeric
  changes were opened and read: `+ added 0.9667805613462681`, grouped
  frequencies with deltas and percentages, `0.0 -> 0.45 +0.45` (no percent of
  zero). Console errors: 0.

Screenshots looked at (`D:/work/sm_qa_rigs/codex/s6b_shots/`):
`j1_today.png`, `j1_today_writes_open.png`, `j1_undo_link.png`,
`j1_day_2026-10-04.png`, `j1_day_2026-10-03.png`, `j1_day_2026-10-02.png`,
`j1_agent_card.png`, `j1_search.png`, `j1_run_card.png`, `j1_run_detail.png`,
`j1_back.png`, `j1_reload.png`, `j1_first_state.png`,
`j2_2026-10-02_added.png`, `j2_2026-10-02_numeric.png`,
`j2_2026-10-01_added.png`, `j2_2026-10-01_numeric.png`.

The rig copies, the archive copy, the base worktree and the Chrome profile were
deleted afterwards; only processes started for this check were stopped.

## 9. Residual risks

Updated by the review round (section 10).

* The ledger stores numbers as SQLite REAL (it treats 1 and 1.0 as one value
  by design, docs/269). A run row shows a whole number as an int when the open
  chip stores an int at that path; a path the open chip does not hold (a key
  only some runs saved, a run of another device) reads as stored, e.g. `331.0`
  -- seen in the browser journey's report. SM write cards show the recorded
  entries, which keep their JSON types.
* Pre-ledger SM edits are counted, never shown as writes. Edits made after
  SM-write recording began but before the chip's first recorded write (a fresh
  chip whose first edits were saves) would also be counted.
* An agent record with neither a run number nor a folder, that finished, is
  matched by node inside its own attempt window and only ever "inferred"; a
  person can still claim that card. Two cards with the same run number show
  the same `#N` on their rows; the folder is named in the opened card.
* A claim made before claims named their card (a bare number) applies only
  where one folder holds that number, or to the run of the folder Datasets has
  open -- the card the old page offered. Elsewhere it stays unapplied.
* Gates on a person's page are "being checked" until the background worker
  reaches them (about 9 s for a 551-run day on this PC); the report and the
  tests check them in the request.
* The report section spans every recorded day and computes every run's gate;
  on a large archive it is slow and large, which is why it is off by default.
  There is no pagination.
* An SM write whose outcome is `failed` or `unconfirmed` is not shown (it did
  not land, or nobody can vouch that it did).
* The S6 check is a bounded sample (90 runs on three days; one QA instance);
  the instance predates S4, so undo marking on real history was exercised only
  in the browser journey.
* Timings describe this loaded PC and these day sizes.

## 10. Review round (2026-10-05)

An adversarial review of `61ad757c` reported 3 P0, 5 P1, 6 P2 and several P3
findings, with repros. Each repro was run RED on `61ad757c` first, then fixed
in new commits on this branch (no rewrite), pinned in
`tests/test_calibration_log_hub.py` (24 new test functions; 62 functions, 87
cases in all) and the jsdom selfcheck `tests/calibration_log_review_selfcheck.cjs`
(driven by `test_review_client_selfcheck`), and mutation-checked.

| # | Finding | Fix | Pin | Measured |
|---|---|---|---|---|
| P0-1 | An agent attempt became the "certain" author of a person's run: the node + time window ignored order, start and failure class | Only an exact run number (with its folder) is certain. A record without one is matched by node + time only when it FINISHED (class `ok` or `unattributed`), only to runs inside `[since - 60 s, ended + 900 s]`, nearest first, one run per record and one record per run, and the result is "inferred". A failed, stopped or refused attempt keeps its own card | `test_a_failed_attempt_never_takes_a_persons_run` (6 classes), `test_a_late_folder_is_inferred_nearest_wins_inside_the_attempt` | browser: the person's run beside a busy attempt reads `unknown`, the attempt has its own "busy" card; a late folder reads `by_claude`, title `inferred` |
| P0-2 | Run numbers collide across data folders: agent records, claims and `#N` journal lines attached to every card with that number | Records carry the run folder (`agent_runs` stores `result.run.folder`; the API's run list returns it); records, claims and numbered lines key by (data folder, number); a claim is made on a card and names it; a number two folders hold is ambiguous -- a bare-number claim or line goes to the Datasets folder's run, else to none, never to both | `test_one_record_names_one_of_two_same_numbered_runs`, `test_claims_and_numbered_lines_belong_to_one_card`, `test_a_bare_number_claim_names_the_run_of_the_datasets_folder`, client selfcheck (the claim carries its card) | browser: two `#253` cards, one `by_codex` (certain), one `unknown`; a claim on the second lands on that card only |
| P0-3 | The redacted report leaked past network values and actor host names: the structural rule knew only the CURRENT documents | For the log section the redactor is widened: every row on a network path is blanked on both sides, its past values join the literal set (hidden wherever else they appear), and a host after `@` in an actor name is hidden | `test_report_hides_past_network_values_and_actor_hosts` | browser: a run whose saved wiring names an older host and cluster -- neither string in the redacted section; the rows read `[hidden] -> [hidden]` |
| P1-4 | Ledger flags were dropped | Each flag is a badge with one line (other chip?, repeats earlier, saved over an SM write, time assumed, rewritten, folder gone, node.json unreadable, no state); a run that may be another chip's lists no rows and is counted apart ("1 maybe another chip's") | `test_ledger_flags_reach_the_card` | browser: the foreign-chip card |
| P1-5 | An idle ledger was presented as complete; with no ledger the page polled forever | Idle: "As last recorded" on the totals and a line saying the window is not updating the history (the report says the same). No ledger: "unavailable", no polling | `test_a_ledger_nobody_updates_is_labelled_and_a_missing_one_never_polls` | |
| P1-6 | The first state was the ledger's first event even when that run saved no state | The first event that HAS a state | `test_the_first_state_is_the_first_event_with_a_state` | |
| P1-7 | The report included lines of a session that named no chip | Left out of the report; the page keeps them under their own heading. The mutation sweep found that these lines were still read by the server's calendar day: now by the project-zone day like every other line | `test_report_leaves_out_lines_of_a_session_that_named_no_chip` | |
| P1-8 | Too slow on a real day: every agent record scanned against every run, node/data files re-read per render, gates computed in the request, 4.3 MB fragments | Agent records indexed by node; a run folder's node/data facts cached per file signature (mtime, size); the rendered day cached and checked before any build; past 150 cards a day sends rows and fetches a card's body on open; gates checked in the background for a person (the day refreshes itself, never under an open card) | `test_many_agent_records_match_by_index_not_by_scan`, `test_a_run_folder_is_read_once_until_its_files_change`, `test_a_very_large_day_sends_rows_and_fetches_bodies`, `test_gates_are_checked_in_the_background_for_a_person`, client selfcheck | table below |
| P2 | Ints shown as floats | Whole numbers read as the open chip stores them at that path | `test_ints_read_as_the_open_chip_stores_them` | |
| P2 | "Parameters changed vs" compared a run of another folder with the Datasets folder | Compared within the run's own folder (one read for the day) | `test_parameters_compare_within_the_runs_own_folder` | |
| P2 | A journal line could show on two days | A line belongs to the project-zone day of its instant; a numbered line may still attach to its run from a neighbouring day | `test_a_journal_line_belongs_to_one_project_day` | |
| P2 | A partly undone write did not say which rows | The rows taken back are marked, per path | `test_a_partly_undone_write_marks_the_rows_taken_back` | |
| P2 | A skipped step counted as a failed run | "did not run", not a run | `test_a_step_that_never_ran_is_not_a_run` | |
| P2 | The log section held the report's shared lock while building every day | Its own lock | `test_the_log_section_never_takes_the_reports_shared_lock` | |
| P3 | Door ids, "--", two "who" on an undo card, mixed boolean styles, "6 writes" for 3 writes | Plain English names, one failure sentence, `true`/`false` everywhere, "3 writes (6 values)", one who per card | `test_plain_words_on_cards_one_who_and_values_vs_writes`, updated earlier pins | |
| P3 | Search matched instants, ledger ids and folder hashes; "undone" was not searchable | Search text is what the card shows, plus undone / partly undone | `test_search_text_is_what_the_card_shows` | browser: `#253` finds exactly the two `#253` cards |
| P3 | Two report checks unpinned (`AVAILABLE_KEYS` vs `SECTION_BY_KEY`) | Pinned: the section route and the final pass both refuse an unknown or unavailable key | `test_report_section_checks_refuse_unknown_and_unavailable` | |
| P3 | A pin used a fixture kind `save` that no door records | The save pin uses a real run and a real undo-journal unit on the server's own day | `test_save_is_not_a_chip_write` | |

`hub_query.timeline` gained `include_ambiguous` (the run numbers two folders
hold) and `previous_in_folder(store, eids)`; its default result is unchanged.

**Measured (P1-8).** Server time of `GET /journal/day` through the Flask test
client on this PC under load, `61ad757c` (a detached worktree) vs this round.
Cold = first request; served = repeats as served; uncached = the rendered-day
cache emptied before each repeat.

| Data | Revision | Cold (ms) | Served (ms) | Uncached (ms) | Fragment |
|---|---|---:|---:|---:|---:|
| Copy of a real 551-run day | `61ad757c` | 2,647 | 422-478 | 603-696 | 4.30 MB |
| | this round | 715 | 122-191 | 158-207 | 1.51 MB |
| Same day + 400 agent records | `61ad757c` | 3,698 | 1,291-1,352 | 1,601-1,712 | 4.57 MB |
| | this round | 656 | 130-171 | 163-186 | 1.80 MB |
| Synthetic 10,000-run ledger (500-run day) + 400 agent records | `61ad757c` | 10,967 | | 9,600-9,900 | |
| | this round | 717 | | 221-281 | |

Every warm request is now under the 300 ms target and every cold one under
1.5 s. A cold request no longer includes the gates: on the real day the
background worker checked all 551 in 8-9 s, after which the page refreshed
itself. One `timeline()` read of the 10,000-run ledger holds the chip's reader
for 33 ms.

The review's own measurement of `61ad757c` on its PC: cold 3.7-4.9 s, warm
0.65-2.3 s, with 50 agent records 2.6-2.9 s, with 400 about 15 s.

**Mutation check.** The first round's 85 mutations re-targeted to the reworked
source plus 49 for this round: **134/134 RED**, 132 by an assertion, 2 by an
exception (the same two as section 6). The first sweep left six GREEN; each was
a pin that could not reach the broken state, not a passing break: two fixtures
put their instant on a different day of the server's calendar than of the
project zone (one of them hid the real gap fixed under P1-7), two mutations had
to follow code that moved, one was re-aimed at the card-body cache key, and one
(the report rendering lazily) was dropped because the report path never asks
for lazy cards.

**Regression.** The same 213 files, serially, one pytest process per
file, `PYTHONUTF8=1 --timeout=900`, jsdom installed: **6,248 passed, 32
skipped, 2 failed** in 2,898 s (the first round: 6,217 / 32 / 2). The two
failures are Windows file-locking flakes that fail on `61ad757c` at the same
rate: `test_instance_move.py::test_two_chips_with_one_folder_name_stay_two_chips`
(`PermissionError` renaming a history folder; 2 of 15 isolated runs on each
tree) and one test of `test_one_run_instant.py::TestRekeyMigration` (section 7;
a different test of that class each time, on both trees). The related set alone
(calibration log, journal, story, chip report, report redaction, report card,
`test_hub_*`, undo journal, agent history restore -- 17 files) on the final
code: 742 passed, 1 failed, then 716 of 716 (15 of the files) on a re-run. The
one failure was `test_hub_sync.py::TestConcurrency`, a timing test (one ledger
connection per burst); it passed 5 of 5 alone and 4 of 4 together with the
log pins, and that class failed once in 4 such runs on `61ad757c` too. Existing tests from origin/main are unedited except the
five report pins of section 4.

**Real browser.** SM from this worktree on 5155, headless Chrome on CDP 9475
with its own profile, `tests/browser/journeys/cdp.cjs` (real mouse events).
Rig: a copy of a real 551-run archive day plus the day before, a second data
folder (the project's storage) holding another `#253`, a chip whose network is
127.0.0.1:1, and four planted agent records; in the copies only, one run's
saved state names another chip and one run's saved wiring names an older host
and cluster. The QUAlibrate project was opened through `/qualibrate/open`, the
history built both folders (`roots=2`). Journey: the day (553 runs counted, 1
"maybe another chip's"; on the first visit 551 gates were checked in the
background and the page refreshed itself, on the visit after the second folder
was added 1 was); the foreign-chip card (badge, line, no rows); the person's
run next to a busy agent attempt; the late folder (inferred); the two `#253`
cards, a claim on the plain one; search `#253`; a run link, Back, reload; the
redacted report (no past host or cluster; `[hidden] -> [hidden]`). Console
errors: 0.

Screenshots looked at (`D:/work/sm_qa_rigs/codex/s6b_shots/`):
`j3_day_top.png`, `j3_foreign_chip.png`, `j3_agent_next_to_person.png`,
`j3_same_number_two_folders.png`, `j3_search.png`, `j3_report_redacted.png`.
