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
| Run | a `run` event | gate verdict, figures, attached journal lines, claims/notes, agent-record author override (as before); its rows are the event's exact change rows (the run's saved state vs the ledger state just before it), each with its op |
| First state | the ledger's first event | "First state in the chip history: N values recorded; there is no earlier state to compare against." The rows are not listed |
| SM write | an event whose kind is in `hub_store.SM_KINDS` and whose outcome is `landed` | actor, kind and door (`src`), every entry SM recorded at the door with each entry's own actor; UNDONE / PARTLY_UNDONE from the ledger flags; an undo or redo names the write it takes back by door and time, linked to that card |
| Agent run without a folder (C-17) | `agent_runs/index.jsonl` merged by key with each Registry `meta.json` (read without constructing the Registry) | "<agent> ran <node> on <targets> -- no run folder (<class>: <error>)", its plan and step, the stated purpose separately; laid on the run row's columns |

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
* **unavailable**: no project zone, or no history dir for the open chip -- one
  line says so (it used to be a 500 for the missing dir).
* **one SM write whose recorded entries cannot be read**: that card says so and
  shows the ledger's own rows for the write; the rest of the day is unaffected.
* **SM edits from before the ledger recorded SM writes**: the undo journal is
  not history (it holds working-copy saves and never says whether one reached
  the chip), so such edits are not cards. A day that has them says how many,
  and from when the chip history records SM writes. Units a recorded write
  names (its journal stamp can precede the event by a few ms) and units after
  recording began are never counted.

Search (docs/276) is unchanged: the server builds one haystack per card and
line with the shared grammar, the page filters as you type, every group and
count follows the filter, folderless agent runs count as runs. A cancelled
agent run is not counted as failed.

The report section `calibration_log` is built by
`routes._report_build_calibration_log`: every day the ledger, the agent
records and the journal know (one `index.postings["day"]` read plus the page's
own agent-record reader), each through `journal_routes._build` -- the page's
day builder -- and rendered with the page's row template. Redaction runs on
its output like every section; its HTML bypasses the chip-state HTML cache
(the ledger and agent records move independently of the state). While the
ledger builds, the section says so and that it is incomplete.

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

* The ledger stores numbers as SQLite REAL, so an integer shows as `40.0` on a
  run row (the ledger treats 1 and 1.0 as one value by design, docs/269). SM
  write cards show the recorded entries, which keep their JSON types.
* Pre-ledger SM edits are counted, never shown as writes. Edits made after
  SM-write recording began but before the chip's first recorded write (a fresh
  chip whose first edits were saves) would also be counted.
* Agent records and claims are keyed by local run id: two registered roots
  holding the same run id can still attribute ambiguously; a record with no run
  id matches by node within 900 s.
* The report section spans every recorded day and computes every run's gate;
  on a large archive it is slow and large, which is why it is off by default.
  There is no pagination.
* An SM write whose outcome is `failed` or `unconfirmed` is not shown (it did
  not land, or nobody can vouch that it did).
* The S6 check is a bounded sample (90 runs on three days; one QA instance);
  the instance predates S4, so undo marking on real history was exercised only
  in the browser journey.
* Timings describe this loaded PC and these day sizes.
