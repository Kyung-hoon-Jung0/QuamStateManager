# docs/267: the tree filter never says "no match" too early

2026-10-04. A folder filtered right after it is added answered "No runs in this
folder match" while matching runs existed. Same code, same data: the answer
depended on machine load, which is why `tests/test_web.py`
`TestWorkspace::test_sidebar_filter_by_status` / `_by_date` /
`_multi_token_with_date` passed or failed from run to run. A user saw the same
wrong "nothing matches".

Codex found the cause and wrote a first fix. The cause holds; the fix was
correct but 4.2-4.6 s per filter request at 5,000 pending runs, and the sidebar
filter runs on every (debounced) keystroke. This doc records the bounded design
that replaced it, what was kept from Codex, and the measurements.

## 1. The race

`POST /workspace/add` calls `Workspace.add_root(defer_parse=True)` (docs/142,
listing-first). Discovery publishes STUB entries at once
(`scanner._stub_entry`): run id, name, time and date come from the folder path
(`#<id>_<name>_<HHMMSS>` under a date dir); status, qubits, pairs and
`filter_params` are empty; `needs_parse=True`. A daemon thread
(`Workspace._hydrate_root`) parses every node.json and swaps the parsed entries
in with one atomic tree rebind, then clears the hydrating flag and bumps
`version`.

`_filter_tree` -> `_entry_matches` read the stub fields as if they were answers.
A stub's status is `""`, so `finished` matched nothing; its date is the folder's,
so `2025` matched by folder, not by `created_at`; `qubit:`/`pair:`/`param:`
matched nothing. With no match left the template rendered
`tree-nomatch-note`. Whether the hydration thread had published before the
filter request decided the answer. The filtered render also passed no
workspace to `_tree_render_ctx`, so the unfiltered "indexing runs..." note
never appeared in a filtered tree to soften it.

The query grammar (`core/search_query.py`, `_parse_tree_query`) was not the
cause: the values being matched were not known yet.

## 2. Why the first fix was not kept

Codex's `scanner.resolve_filter_entries` parsed EVERY pending entry inside the
request before matching. Correct, and the three tests went green, but at 5,000
pending runs one request cost 4.2-4.6 s (Codex's measurement; this round's
harness measured the same design at 1.05-1.78 s on a quieter machine, section
5). SM's order is correctness first, then speed: a seconds-long keystroke is
not acceptable when an honest partial answer can be given in ~0.1 s. Codex's 5,000-run pin also
asserted `len(parsed) == 5000`, i.e. it pinned the slow behaviour.

Kept from Codex: the cause analysis; "a filter request never publishes" (stubs,
`tree`, the path index and `version` stay untouched -- hydration and rescans own
publication, and an older reader may still hold the stub snapshot); regrouping
parsed runs by node.json's date; reusing the scanner's bounded parse pool; the
pin technique of HOLDING the real hydrator with Events; its eight-query
parametrization.

## 3. The design

`_filter_tree(tree, text, pending_out=None)` (routes.py):

1. Collect the stubs of every root. A tree with no stubs takes exactly the old
   path (one extra `getattr` per entry).
2. Order the stubs: those whose folder-derived fields already match the query
   first (a folder name almost always agrees with node.json's name), then
   newest run first.
3. Parse at most `scanner.FILTER_INLINE_PARSE_MAX` (32) of them inline through
   `scanner.parse_stubs` (the same `_SCAN_PARSE_WORKERS` pool as the scanner).
   A COUNT, not a wall-clock budget: which runs a request resolves must not
   depend on machine load -- that dependence is the flake being fixed. A folder
   of 32 runs or fewer (the common add, and all three flaky tests) is answered
   exactly.
4. Judge only parsed entries. Every stub not read is neither matched nor
   rejected: it is counted into `pending_out[root]`.

`_filtered_tree_ctx(ws, name_filter)` is the one render context for
`/workspace/tree` and `/workspace/refresh`. It adds `tree_pending` (the counts)
and `tree_pending_live`: the roots whose background hydration is still running,
or for which `version` moved while the request read an older snapshot. The
template (`_sidebar_tree.html`) then shows, above the matches:

- live: `⌛ 1,968 runs still being scanned — results may grow`. The note
  refetches the tree 2 s after it loads (`hx-trigger="load delay:2s"`,
  `hx-include="#sidebar-filter-input"`) in the filter box's own sync group
  (`hx-sync="#sidebar-filter-input:replace"`), so a refetch can never land
  over a newer keystroke. While hydration runs the refetch is a filtered-HTML
  memo hit (same version, same query); the hydration's own version bump makes
  the next refetch render the full answer, and a tree with nothing pending
  carries no refetch. This is the same self-refetch the unfiltered
  "indexing runs..." note already used; no new poll or endpoint.
- not live (a hydration that failed and left stubs): `⚠ 2 runs not read yet —
  results may be incomplete`, with no refetch loop. It does not promise that
  the ↻ button fixes it: `_incremental_rescan` keeps an unmoved stub as a stub
  (pre-existing, out of scope here).

"No runs in this folder match" renders only when nothing is pending.

## 4. Pins and mutations

`tests/test_tree_filter_race.py`, 16 pins. Every pin that needs the stub state
holds the real `Workspace._hydrate_root` on an Event before it parses anything,
so the race is reached on every run, never by luck.

| Pin | What it holds |
|---|---|
| `test_a_small_folder_is_answered_exactly_before_hydration[8 queries]` | the three flaky tests, deterministic: two runs whose folder name/date contradict node.json; `finished`, `2025`, `2025 resonator`, `name:`, `qubit:`, `pair:`, `param:`, `-status:` each show exactly the matching run, no note, and the live tree is still all stubs afterwards |
| `test_a_stub_is_never_judged_on_its_folder_name` | the folder-derived name (`placeholder`) and date (`date:2026-01`) match nothing; with everything read, "no match" is a real no |
| `test_past_the_cap_the_tree_says_pending_never_no_match` | cap 2 of 5: `data-pending="3"`, "3 runs still being scanned", never "no match", for a status, a nonsense and a qubit query; an unread stub's `placeholder` name is not judged; the note carries the refetch and the filter's hx-sync; the Refresh door renders the same |
| `test_likely_matches_are_read_first` | cap 2 of 8: the two stubs named `target_scan` are read, not the two newest |
| `test_once_hydrated_the_right_matches_show` | after release: matches [1, 3, 5], no pending line, a real "no match"; an older stub snapshot is answered the same bounded way, regrouped by node.json's date, and never rewritten |
| `test_a_failed_hydration_says_not_read_and_does_not_poll` | "2 runs not read yet", no "still being scanned", no refetch |
| `test_a_run_that_cannot_be_parsed_stays_unread_not_a_500` | a node.json of an unexpected shape (`metadata: null` raises in the parser) leaves that run unread and counted; the request is 200 and the other run is answered |
| `test_a_snapshot_published_over_mid_request_refetches` | a version move during the request keeps the refetch |
| `test_5000_pending_runs_cost_at_most_the_cap_in_reads` | 5,000 stubs: exactly 32 reads, the newest 32 when no stub looks like a match, `pending == 4,968`; a parsed 5,000-run tree reads nothing |

Base code: **15/15 RED** (the fifteen pins written first; the sixteenth pins
a guard the base code has no counterpart of). Fixed code: **16/16 GREEN**.

Mutations (each applied to a scratch copy of the repo, the whole pin file run,
the original bytes restored and compared):

| Mutation | RED |
|---|---:|
| M1 no inline parse (cap -> 0) | 15/16 |
| M2 judge unread stubs on their placeholders | 1/16 |
| M3 no likely-match priority (newest only) | 1/16 |
| M4 pending count never reported | 7/16 |
| M5 template: "no match" ignores pending | 2/16 |
| M6 template: live note without the filter's hx-sync | 1/16 |
| M7 every pending root refetches (no failure carve-out) | 1/16 |
| M8 version move ignored | 1/16 |
| M9 Refresh door keeps the old render | 1/16 |
| M10 the filter publishes into the live stubs | 13/16 |
| M11 parsed runs keep the stub's date group | 1/16 |
| M12 parsed entries re-read too | 1/16 |
| M13 unbounded inline read (Codex's design) | 5/16 |
| M14 a raising parse is not contained | 1/16 |

**14/14 mutations killed; 16/16 pins killed by at least one mutation.**

## 5. Measurements

Harness: 5,000 synthetic runs (`<root>/2026-09-DD/#<id>_<name>_<HHMMSS>/`
with node.json + quam_state, five experiment names, odd ids `finished`, qubits
q1-q5), local SSD, the REAL route (`POST /workspace/add`, then
`GET /workspace/tree?name=<q>` through the Flask test client), the
filtered-HTML memo cleared before every request so each one is a full render.
"Held" = the hydrator held before it parses anything (the pure pending path);
"live" = a fresh add, the five queries fired at once while hydration runs (3
adds); "parsed" = after hydration published. Machine shared with other work;
numbers are medians unless stated.

Held hydration -- the pending path (ms; what the tree showed):

| Query | Base (wrong answers) | Read everything (Codex) | This fix |
|---|---|---|---|
| `finished` | 9.9; "no match" | 1,198; 2,500 runs | 40.0; 16 runs + "4,968 runs still being scanned" |
| `rabi` | 40.8; 1,000 runs judged on folder names | 1,131; 1,000 | 42.0; 32 + pending |
| `2026-09-03` | 17.9; 250 judged on the folder date | 1,053; 250 | 41.9; 32 + pending |
| `qubit:q1` | 8.7; "no match" | 1,071; 1,000 | 39.6; 6 + pending |
| `zzzzqq` | 9.9; "no match" | 1,056; "no match" | 34.0; pending, never "no match" |

This fix, held: median 39.6 ms, max 84.6 ms over 15 requests.

Live (hydration's own 32-thread parse running in the same process): median
97-155 ms across three sets of 15 requests, p90 ~200 ms, max 204-245 ms. The
slow requests are the first one or two after the add, while hydration spins
up; in the two live-only sets the first request took 198-231 ms and every
later one 70-121 ms. A cap of 16 instead of 32 cut the median by ~20 ms and
did not remove that ~200 ms first request (max 191-208 ms), so the cap stays
at 32 (more folders answered exactly). In the browser the first filtered
request arrives at least the 250 ms debounce after the add.

Parsed path (the old path; must not move) -- 20 requests per query per
process, interleaved base/fix/base/fix:

| Query (matches) | base | fix | base | fix |
|---|---:|---:|---:|---:|
| `finished` (2,500) | 103.8 | 104.1 | 120.5 | 111.7 |
| `rabi` (1,000) | 45.3 | 50.2 | 52.9 | 56.7 |
| `2026-09-03` (250) | 19.0 | 20.6 | 18.9 | 19.5 |
| `qubit:q1` (1,000) | 44.0 | 44.8 | 42.5 | 49.3 |
| `zzzzqq` (0) | 10.8 | 10.9 | 10.8 | 11.2 |

Within run-to-run noise; the only added work is one `getattr` per entry.
`test_5000_pending_runs_cost_at_most_the_cap_in_reads` pins the bound itself
(32 reads at 5,000 pending, 0 on a parsed tree) without a wall-clock assert.

## 6. Validation

Env `cqt` (Python 3.12.13, pytest 9.1.1, pytest-timeout 2.4.0), `PYTHONUTF8=1`,
`--timeout=900 --timeout-method=thread -p no:cacheprovider`. No existing test
was edited.

- **The three flaky tests, unchanged, 20 separate invocations: 20/20 green**,
  twice -- once while the mutation sweep ran beside it (load), once on the
  final code.
- **Every test file mentioning workspace / scanner / sidebar / search_query /
  tree**: 223 files (case-insensitive, recursive -- the same 223 Codex's `rg -i`
  found; Codex then left five out, all five ran here). Five invocations, run
  concurrently: 7,446 tests (= the collect-only count below), 7,355 passed,
  90 skipped, 1 failed. The failure,
  `test_safe_io.py::TestTwoWritersOfOneFile::test_concurrent_writes_of_one_file_all_land`,
  is a `ws_cache.json~RF*.TMP` left by Windows `ReplaceFileW` under five
  concurrent pytest processes plus a mutation sweep; it is in code this change
  does not touch and passed 5/5 alone and in its whole file. After the last
  code change (the parse guard) the 17 files nearest the filter were re-run
  together: 832 passed, 30 skipped.
- **The 0xC0000005 crash.** Codex reported its single 223-file invocation dying
  with an access violation during collection. Its launcher
  (`.tree-filter-validation/validate.py`, scratch, deleted after this round)
  did not run the given command: it wrapped pytest in
  `faulthandler.dump_traceback_later(60, repeat=True)` (and stubbed
  `platform._wmi_query`). Reproduced here:
  - the given command over all 223 files, `--collect-only`: 7,446 tests
    collected in 126 s, exit 0; the five shards above ran every one of them;
  - the same under Codex's 60 s watchdog: one dump at 60 s, survived;
  - the same under a 2 s watchdog: **the process died in 3-9 s, twice in
    bash (SIGSEGV) and once under PowerShell (exit code `0xC0000005`)**; in
    the first bash log the "Windows fatal exception: access violation" line
    cuts a `File "..."` line of a dump in half -- the shape of Codex's log.
  The crash comes with the watchdog and never without it, and it lands inside
  a dump: the watchdog thread walking the other threads' frames while they run
  (inferred from where it lands, not traced further). Collection takes ~126 s,
  so Codex's first 60 s dump fell mid-collection. It is not SM and not the test
  selection. Do not launch the suite under `dump_traceback_later(...,
  repeat=True)`.
- **Real browser** (headless Chrome over CDP on 9445, SM served from this
  worktree on 5125, sandboxed `USERPROFILE`/`HOME`, a scratch instance and a
  2,000-run synthetic workspace; real mouse clicks and real text input through
  `tests/browser/journeys/cdp.cjs`). The per-root listing cache would serve
  parsed entries on a re-add, so it was cleared before each cold add. Add the
  folder, type `finished` at once:
  - 0.4-0.55 s after typing (incl. the 250 ms debounce): `⌛ 1,968 runs still
    being scanned — results may grow` above 16 confirmed matches, refetch
    attribute present, no "no match";
  - the note's own refetch 2 s later (server log: one more
    `/workspace/tree?name=finished`): 1,000 matches in 10 date groups, no note;
  - with `zzzzqq`: the pending line first, then "No runs in this folder match
    “zzzzqq”." only after the scan finished;
  - away to `/datasets` -> back -> reload: tree intact (2,000 runs, 20 groups),
    the same query on the hydrated tree answers 1,000 matches ~0.35 s after
    typing (incl. the debounce);
  - console: 0 errors after the harness's documented filter; the raw stream
    held only the known CSP `EvalError` lines (htmx compiling `hx-trigger`
    filter expressions, docs/175). The new note's trigger, `load delay:2s`,
    has no filter expression.
  The browser round ran before the parse guard (section 4, M14) was added; the
  guard changes nothing on a path where no parse raises.

## 7. Not done here

- Partial answers stay partial until hydration publishes (one atomic rebind,
  docs/142). Publishing hydration progressively would let a slow share show
  growing results every refetch; it changes docs/142's publication contract and
  was not needed for correctness.
- A hydration that fails leaves stubs until a structural rescan or a restart
  (pre-existing; the note now says so instead of "no match").
- Observed in the browser round, not fixed: the add-folder box's path
  suggestions (`initPathAutocomplete`) fetch 250 ms after typing and can open
  AFTER the box lost focus, so a path pasted and submitted quickly leaves the
  suggestion list open over the compare buttons and the filter box until
  Escape.
