# Review handoff — docs/141 §4m … §4ab (branch `fix/wiring-diagram-crop`)

Written 2026-08-30 for a NEW session running an adversarial review at high
effort. Everything below is what that session needs to start without asking.
The author's own claims are the targets; treat this file as a map, not as
evidence.

## 0. The one-paragraph brief

Review commit range **`b1b9050^..HEAD`** (18 commits; `git log --oneline b1b9050^..HEAD`) in the worktree **`D:\work\statemanager-rv128`**. Each commit
has a matching section in `docs/141_night_session_manual_speed.md`
(§4m–§4ab) and a paragraph in `CLAUDE.md`. Method = docs/141 §4l-review:
several reviewers, each claim re-executed (real Chrome where the claim was
measured in real Chrome), pins mutation-checked, findings ranked, THEN one
fix commit. Report first; do not fix while reviewing.

## 1. Ground rules (binding)

- Work only in `D:\work\statemanager-rv128`. **Never touch `D:\work\statemanager`**
  (another session's checkout) or `D:\work\statemanager-cfb`.
- Never write into customer trees (`D:\work\Customer_Codes\...`,
  `D:\2025-06-24`) — read-only inputs. `D:\work\documentation-website` is
  read-only.
- No bare `git stash` (shared stash stack). No push, no merge — the user decides.
- Tests: conda env **`cqt`**, always `PYTHONPATH= PYTHONUTF8=1`, e.g.
  `PYTHONPATH= PYTHONUTF8=1 conda run -n cqt python -m pytest tests/test_x.py -q --timeout=900 --timeout-method=thread -p no:cacheprovider`.
  `conda run` does not pass heredoc stdin — write a script file. Two
  `conda run` invocations in parallel can race on conda's tmp file
  ("Failed to run conda activate") — retry, it is not a code failure.
- jsdom harnesses: `node tests/<name>_selfcheck.cjs` (94 files). Loop:
  `for f in tests/*_selfcheck.cjs; do node "$f" >/dev/null 2>&1 || echo "FAIL $f"; done`.
- Bash heredocs on this machine EAT backslashes (`\b` → backspace) — write
  anything with backslashes via a file/Write tool. A corrupted regex from
  exactly this bit the author twice today (docs/141 §4ab notes one).

## 2. Known state (so you do not chase ghosts)

- Pre-existing failures, NOT regressions: `tests/test_web.py::TestPhase4QuamCacheConcurrency::test_concurrent_activate_quam_keeps_one_entry`,
  `tests/test_web.py::TestDatasetSelectionFix::test_dataset_payload_carries_active_folder`
  (tmp-path case-identity class, docs/87), `tests/test_compare_hub_routes.py::TestHubShell::test_label_html_is_escaped`
  (Windows cannot create a `<script>` folder name; fails on HEAD too).
- `tests/version_diff_selfcheck.cjs` is intermittent (passes ~half the runs,
  exits 0 with all 48 oks when it passes; it evaluates only app.js, which
  §4m–4ab did not change after its last green loop). Timer-dependent; a
  finding if you can root-cause it, not a regression of this range.
- Baselines measured on this branch: 94/94 harnesses (modulo the flake);
  `test_web.py` 524 passed / 2 known failed; the diff/compare/sidebar suites
  (`test_diff_workbench`, `test_diff_three_way`, `test_diff_panes`,
  `test_sidebar_compare`, `test_compare_hub_routes`, `test_bundles`,
  `test_misc_ui`, `test_state_versions`, `test_multifolder_datasets`,
  `test_ds_flow`, `test_dataset_apply_to_chip`) green except the one above.

## 3. The commits and what each claims

| commit | § | claim to attack |
|---|---|---|
| b1b9050 | 4m | /bulk document 8.98→3.90 MB with a per-td golden of 7,810 cells, 0 diffs (`scratchpad/bulk_golden.py`); lazy `.bulk-ba`/`.bulk-band-msg` |
| 3e3a337 | 4n | server-side column virtualization of the qubit grid: `core/bulk_virt.py` planner mirrors `_virtInit` conservatively; `GET /bulk/cells`; cold render + hydration == hot render (golden); grid memo key |
| be222a9 | 4o | Chip Status re-layout; `assignment_fidelity_gef` metric + history index v4 migration; per-panel density |
| 9120f00 | 4p | `core/run_watch.py` stat watcher + `/datasets/wait` long-poll (`since=-1` handshake); popup 221–544 ms after a folder appears |
| 3a5437e | 4q | ONE vertical scroller on Live Edit + Chip Status; toolbar rows follow sideways scroll by translateX |
| e48fdb8 | 4o' | fine slider restored; floors 0.35 / 0.25× |
| 2012a3d | 4r | (superseded by 4y) |
| 0c72989 | 4s | popovers no longer painted over (explicit z-order, no will-change); Pairs picker |
| 602b1ce | 4t | Collections tag row only with tags |
| 2a51a60 | 4u | `float-panel.js` one drag core; Settings floats; Calculator no longer closed by Settings (two paths) |
| a248d3c | 4v | `.ch-card.tfx-host` one frame |
| 60db105 | 4w | tree `?` hover-only, re-appended after the lazy action group |
| f6f461f | 4x | `enhanceColumnResize`: table width = sum of columns once manual |
| 0e47dd6 | 4y | Compare Selected → `/diff` for 2–5 (`HX-Location` into `#table-pane`), figures first, sidebar ticks kept (sessionStorage mirror), 6th tick refused, Compare hub retired as a destination |
| 7243bbe | 4z | the pane view: N panes, server equality groups (`_diff_row_groups`), client baseline switch, `htmx:configRequest` rewrite, Δ only where differing |
| 46c5fb7 | 4aa | sidebar root row: name first, parent ellipsized, reserve rule outranks `#sidebar details > summary` |
| 937995e | 4ab | Keys column as a tree (`_diff_tree_rows`), client ancestor-walk collapse, Depth buttons |
| HEAD | 4ab' | keys share the value cells' face/size, weight 500/600 (user feedback) |

## 4. Where the author thinks it is weakest (start here)

1. **§4x `fitTableToColumns` uses `th.offsetWidth` to freeze unpinned columns.**
   If `enhanceColumnResize` ever runs while the table is `display:none` (a
   hidden tab, a collapsed section) with saved widths present, unpinned
   columns freeze at **0 px** and the table width becomes the sum of the
   pinned ones only. Check every caller (`pulses.js:~1248`) and the htmx
   re-render timing. Not verified by the author.
2. **§4y sessionStorage restore keys on the checkbox VALUE (the quam_state path).**
   Two roots containing runs with identical paths cannot happen, but a
   filter-narrowed tree re-render restores ticks on boxes that are now
   hidden — is the count then honest? Also: `restore()` never prunes stale
   paths; the mirror grows until Clear.
3. **§4y `HX-Location` + `window.Bundles` (docs/141 §4l).** `HX-Location`
   makes htmx issue its own request; the bundle hold works through
   `htmx:confirm`. Verified in real Chrome only for `/diff` from `/datasets`.
   Try it from a page whose bundle is NOT yet loaded (e.g. from `/pulses`),
   and Back/Forward after it.
4. **§4z `_diff_row_groups` is O(sides × groups) per row with `json_diff._eq`**
   → `differ.compare_equal` per pair. 5,000 rows × 5 sides is fine; check
   that `compare_equal` on nested lists (a list-valued leaf) does not throw
   and that the row cap (`ROW_CAP` 5,000) + paging (300) + the tree (§4ab,
   built per page) never disagree about counts shown to the user.
5. **§4z data tab for N sources** diffs the per-file variable inventory
   dicts (`_diff_side_doc` for `data`) — semantics were never looked at for
   N>2; is a row there meaningful?
6. **§4ab `_diff_tree_rows` "value that is also a container" branch** (a
   leaf `a.b` on one side, `a.b.c` on another): pinned by a unit test, never
   seen on a real chip. Also: paging — "Show more" re-renders the whole
   page with `rows=600`; the tree is rebuilt from the first 600 rows, so a
   container's `count` is the count WITHIN THE PAGE, not the true count.
   The note above the table says nothing about that.
7. **§4ab CSS `!important` on `.dp-key-col` padding** — check it does not
   defeat the sticky header or the scroller in narrow panes (real Chrome,
   1,200 px wide).
8. **§4y the retired Compare hub**: routes/templates/tests still exist
   (`/compare-hub`, `/datasets/compare`, `compare-hub.js` still in the
   'compare' bundle). Any UI path that still reaches them is a finding
   (the author removed the sidebar/Datasets/workbench/Versions links only
   where grep found them). `_HUB_MAX_SOURCES` is now unused in `/compare`.
9. **§4p `/datasets/wait`** holds a worker thread up to 25 s per client tab;
   with `threaded=True` dev server that is fine, but count the threads under
   the pywebview desktop launcher and with several tabs open.
10. **§4n `bulk_virt.plan` conservatism**: the claim is "cold columns are
    only those the client would have virtualized anyway"; the golden proves
    hydration parity, not that the cold plan is never WIDER than the
    client's (a too-wide plan = visible empty cells on a wide monitor).
    Re-derive with `vw=` values above 1920.
11. **§4u/§4v/§4aa cascade fixes** were each "raise specificity / move the
    rule"; grep for the NEXT rule that now loses (e.g. anything targeting
    `.tree-root-label` or `.calc-popover` after the unified frame rule).
12. **§4w**: the `?` is re-appended inside `_buildRowActions`; rows whose
    actions are never built (non-crud trees don't have a `?` at all — fine)
    and rows rebuilt by an htmx swap while hovered.

## 5. How to reproduce in real Chrome (the author's tooling)

Headless Chrome with CDP, driven by Node scripts (the Chrome extension
cannot reach this machine's localhost):

```
# demo server on the worktree (restart after ANY routes/template change; static JS/CSS is live)
cd /d/work/statemanager-rv128
PYTHONPATH= PYTHONUTF8=1 SM_DISABLE_ENV_WARMUP=1 nohup conda run -n cqt python -c "from quam_state_manager.web.app import create_app; create_app().run(port=5199, threaded=True)" > server.log 2>&1 &
# kill it by PID: powershell "Get-NetTCPConnection -LocalPort 5199 -State Listen | Select -Expand OwningProcess" -> Stop-Process

# headless chrome (never taskkill chrome.exe wholesale -- filter on the chrome_prof profile)
"C:\Program Files\Google\Chrome\Application\chrome.exe" --headless=new --remote-debugging-port=9333 --user-data-dir="<scratch>\chrome_prof" about:blank
```

Scripts (this session's scratchpad — copy them out, the directory is
session-scoped):
`C:\Users\KyunghoonJung\AppData\Local\Temp\claude\D--work-statemanager\16dead20-945e-4537-8568-fefa684998ae\scratchpad\`
— `cdp_panes.js <loadFolder> <runsRoot> [port] [nTicks]` (pane view, baseline
switch, tree collapse), `cdp_sidebar_diff.js` (ticks kept, 6th refused),
`cdp_rootrow.js` (root-row overlap), `cdp_colresize.js` (one column moves),
`cdp_treehelp.js` (tree `?`), `cdp_float.js`, `cdp_popover.js`,
`cdp_schema_modal.js`, `cdp_wake.js`, `cdp_scroll.js`, `cdp_bulkload.js`,
`cdp_hydrate.js`, plus `bulk_golden.py` (capture/compare per-td tokens).
Every script opens its own tab via `/json/new` and MUST `Target.closeTarget`
at the end (66 stale tabs were found once). Run with `MSYS_NO_PATHCONV=1
node <script> 'D:\work\Customer_Codes\PJ_10082026\quam_state' 'D:\2025-06-24' 5199`.

Inputs: chip = `D:\work\Customer_Codes\PJ_10082026\quam_state` (20Q, the
baseline chip); runs = `D:\2025-06-24` (101 qualibrate run folders, the ones
in the user's screenshots: #1222–#1226). A/B against the pre-change tree:
`git worktree add --detach <scratch>/wt_head b1b9050^` served on 5198.

## 6. Deliverable

1. `docs/141_night_session_manual_speed.md` gets a new section
   `## 4ac. Review round over §4m–§4ab` in the §4l-review format: a numbered
   finding list (severity CRITICAL / MAJOR / MINOR, the executed
   reproduction, the file:line, the fix), then the fix commit(s).
2. Every fix carries a pin that FAILS without it (mutation-check it and say
   so), and the pins listed in §3's docs sections must still pass.
3. Re-run the two real-Chrome checks the user watches most: `cdp_panes.js`
   with 5 and with 2 ticks, `cdp_sidebar_diff.js`.
4. Update the `CLAUDE.md` night paragraph and the memory file
   `C:\Users\KyunghoonJung\.claude\projects\D--work-statemanager\memory\apply-server-time-plan.md`
   (status line + review target).
5. Answer the user in Korean (they write Korean); code/docs stay English;
   no LaTeX.

## 7. What is deliberately NOT in scope

- The Versions panel's own N-way (`/diff/versions`, docs/128) — untouched,
  its pins (`version_diff_selfcheck.cjs`, `TestVersionsCompareNway`) stand.
- Physical deletion of the Compare hub code — a separate confirmed commit.
- app.js split (maintainability call, user's decision pending).
- Autofit / knowledge work (docs/129–140) — a different branch.
