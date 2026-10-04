# docs/276 -- Calibration log search as you type

2026-10-04. Finding C-16, on `fix/log-search`. Production changes are limited
to `web/static/journal.js`, the two Calibration log templates, and the read
helpers in `web/journal_routes.py`.

## Cause and behavior

The form's htmx trigger sent search changes back to the server, and the
server used one substring test for run/write cards. Journal lines and the
timeline were rendered from the whole day. The empty-state decision looked
only at cards, so matching lines could sit below "Nothing matches".

The search now uses `SearchQuery` with a 150 ms input debounce. Enter, form
submission, and the search input's clear action apply immediately. Spaces
mean AND; a standalone `|` means OR, with the same binding and literal-pipe
rules as the other SM search boxes. The read route uses the Python twin,
`core/search_query.py`, for an initial query or a day request.

Each run card, write card, attached journal line, loose journal/agent line,
and unassigned line has a server-rendered search haystack. The client
caches those strings for the current body and filters them with one query.
Timeline pills follow their run cards; segments, target rows, section
headers, and line groups disappear when they have no matching contents.
"Nothing matches" appears only when no card or independent line matches.

Run and journal-line counts describe the filtered set. The target expansion
button counts matching targets. Day statistics and the new-since-visit
marker are explicitly labelled as day totals. A line-count label wraps the
whole sentence, preserving the existing unassigned-line wording and its
singular/plural forms.

An initial server filter keeps excluded cards in an escaped JSON HTML
cache. The client restores them once, in their original order, and binds
their path popovers and htmx links. Clearing a query therefore restores the
day without another request. All unassigned lines are available, including
lines beyond the previous six-line preview. A day navigation still fetches
the new body; its swap rebuilds the search index using the current query.

## Pins and mutations

`tests/journal_search_selfcheck.cjs` drives the shipped `search-query.js`
and `journal.js` under jsdom against HTML rendered by the real Flask read
routes and templates. `tests/test_journal_search.py` supplies a synthetic
day containing every group and wraps the selfcheck in the existing
Node/pytest style.

The selfcheck has 24 independent pins:

| Area | Pins |
|---|---:|
| Input debounce, latest burst, Enter, form submit, no search request | 5 |
| AND/case, standalone OR, literal embedded pipe | 3 |
| Human/agent loose lines, attached lines, unassigned tail/count | 3 |
| Line-only, write-only, unassigned-only, truly empty results | 4 |
| Run header, timeline/target count, clear, labelled day totals | 4 |
| Author changes, day navigation, cached order/links, body swap | 4 |
| Python/JavaScript grammar parity on the real read route | 1 |
| **Total** | **24** |

Every pin was broken independently in a scratch source/template copy,
required to fail with its own assertion, and restored. The wrapper was
also checked against a silent successful Node exit: it correctly failed.
**25/25 pins mutation-checked, 25/25 targeted mutations RED.** Scratch bytes
were restored and the production bytes compared after every trial.

The resumed draft's nested count span and damaged dash failed existing
page tests during development. Both were corrected in production markup;
no existing test was edited.

## Real browser and timing

SM was served from this worktree on **5141**, using a copy of the supplied
chip under `D:/work/sm_qa_rigs/codex/logs_rig/search_check`. The SM process's
`USERPROFILE` and `HOME` were sandboxed; the copied chip's network stayed
`127.0.0.1:1`. Chrome used an isolated user-data directory.
Headless Chrome used CDP on **9461**. Ports 5060/5061 were not used.

The requested `D:/work/statemanager/tests/browser/journeys/cdp.cjs` did not
exist. The existing `tests/browser/journeys/cdp.cjs` in this worktree was
used, including its **`b.errors()` function**.

The browser day contained 500 synthetic runs, two write cards, human and
agent loose lines, unassigned lines, and two attached lines. Typing
`ramsey` letter by letter produced 250 runs, one write card, two loose
lines (one human, one agent), one unassigned line, one attached line,
four matching targets, and 250 timeline pills. The run header read 250;
the day statistics remained labelled as totals. Clearing restored all
groups, and reloading preserved the page. Search issued **zero
`/journal/day` requests**. `b.errors()` returned **0 console errors**.

For timing, two synthetic run cards were removed from the browser DOM,
leaving **exactly 500 run/write cards**. Twenty real input events alternated
`zzzz-no-match` and Select All + Backspace. The probe asserted zero visible
cards after the unmatched query and 500 after every clear. Timing starts at
the input event and ends
after two animation frames following the DOM update, so it includes the
150 ms debounce and a painted frame. The final measurement ran after the
regression selection had finished.

| Round | Samples | Median | p95 | Maximum |
|---|---:|---:|---:|---:|
| Hide/restore all 500 cards | 20 | 250.0 ms | 296.5 ms | 365.1 ms |

An earlier single-letter timing probe could match saved metadata. Its
numbers were discarded in favor of the verified zero-match probe above.

Screenshots and the final timing samples are retained under
`D:/work/sm_qa_rigs/codex/logs_shots/`:

- `journal_initial.png`
- `journal_ramsey.png`
- `journal_clear.png`
- `journal_reload.png`
- `journal_timing.json`

Only the server and Chrome processes started for this check were stopped.
The copied chip's files and directory were deleted. Automatic safety
review refused recursive directory cleanup with "blocked by policy";
the remaining rig artifacts and both Chrome profiles are still under
`D:/work/sm_qa_rigs/codex/logs_rig/search_check`.

## Regression validation

Python: `D:/miniconda3/envs/cqt/python.exe`, `PYTHONUTF8=1`,
`pytest -p no:cacheprovider --timeout=900`. No faulthandler watchdog.

The literal case-insensitive grep selection was run, covering every
existing Python test file mentioning journal, story, or calibration log
(147 files). The new wrapper was run separately, and the focused page,
story, and wrapper selection passed **75/75** tests. The wrapper passed
again after the mutation checks.

```powershell
$env:PYTHONUTF8 = '1'
$journalTests = @(rg -l -i 'journal|story|calibration log' tests -g 'test_*.py')
& D:/miniconda3/envs/cqt/python.exe -m pytest -p no:cacheprovider --timeout=900 `
  -q --tb=short @journalTests
```

Every matching `tests/*journal*selfcheck.cjs` and
`tests/*story*selfcheck.cjs` also passed: **7 scripts, 83 assertions/pins**.
The latter glob includes the five matching history selfchecks.

The 147-file selection collected **4,994 tests** and completed with
**4,961 passed, 32 skipped, 1 failed** in 2,261.47 s. The new wrapper adds
one separately passing test, for 148 Python files covered in total.

The existing failure was
`tests/test_one_run_instant.py::TestRekeyMigration::test_a_taken_target_is_a_collision_and_nothing_merges`,
at line 418. The captured log shows `PermissionError: [WinError 5] Access
is denied` in `core/history_rekey.py::_move_dir` while renaming a history
directory. The migration stopped before creating the expected collision
target. Neither that test nor the migration code was changed.

The same test passed in isolation (**1/1**), and its full file then passed
(**31/31**), without edits. The broad run's failure is retained here rather
than described as a completely green first run. There were no failures in
the focused Calibration log/story checks or in the requested Node checks.
