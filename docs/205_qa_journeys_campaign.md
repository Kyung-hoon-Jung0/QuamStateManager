# docs/205 — The real-browser QA campaign: journeys, two fix waves, a reverify

2026-09-25. Following docs/203/204 (both found by a customer clicking through
the app, not by a feature test), the QA method itself became the deliverable:
walk real user journeys in real Chrome across every surface, fix what breaks,
then re-walk every fix to check it actually holds.

## 1. The journeys harness (`tests/browser/journeys/`)

- **`cdp.cjs`** — the CDP driver every journey script imports. No puppeteer:
  Node's built-in WebSocket against `chrome.exe --headless=new
  --remote-debugging-port=<port> --remote-allow-origins=*`. Every click is a
  REAL `Input.dispatchMouseEvent`, never a synthetic `.click()` — a synthetic
  click skips hit-testing, so it "works" on a button hidden under an overlay,
  which is exactly what a journey must catch.
- **`crawl.cjs`** — "click everything, then find your way back." Written
  because docs/204 shipped with a feature check that the Trends click OPENED
  the run, and a selfcheck pinning where — nobody pressed the × afterwards.
  A feature check asks "does X happen"; a user asks "and then how do I get
  back". Per clickable target: fresh load → scroll the pane (lazy sections
  build) → one real interaction → what opened? → the way back a person would
  try (its own close control, then Escape, then click outside, then browser
  Back) → is the page still whole, and did anything log a JS error?
- **`chip_status.cjs`** — curated Chip Status journeys (a Trends point → its
  run → its tabs → × → Trends where it was; Back restoring the section, tab
  and scroll offset after a refresh; the threshold editor not blocking a
  refresh while closed).

## 2. Wave-1 and wave-2 fix packages, per surface

330 findings were confirmed (refuted duplicates and by-design reports
excluded) across seven surfaces, then fixed by parallel packages — wave-1
first (three packages, mostly Live Edit + the Re-generate backend), wave-2
the rest, wave-2c a short cleanup pass:

| surface | confirmed | fixed | partial | skipped | packages |
|---|---|---|---|---|---|
| chipstatus | 47 | 46 | – | 1 | cs-data, cs-ui |
| datasets | 50 | 50 | – | – | ds-run, ds-table |
| diagnostics | 37 | 34 | – | 3 | dg-core, dg-nav |
| generate | 46 | 44 | – | 2 | gen-session, gen-wiring, gen-populate, gen-regen, gen-backend |
| jsontree | 49 | 44 | 1 | 4 | jt-edit, jt-view |
| liveedit | 42 | 38 | 3 | 1 | live-grid, live-sync |
| regenerate | 59 | 56 | 1 | 2 | regen-backend, gen-session, gen-wiring, gen-populate, gen-regen |
| **total** | **330** | **312** | **5** | **13** | |

("skipped" is mostly "owned elsewhere, verified fixed by the owning
package" or "by design, doc-quoted" — see `reverify_all.json`'s
`claimed_reason` field per finding.) Merged into `qa/integration` as a chain
of `merge fix/qa2-*` / `fix/qa3-csperf` commits.

## 3. Re-verifying all 330

`reverify.js` re-walked every one of the 330 confirmed findings' ORIGINAL
repro steps against the merged build in real Chrome (own rig per chunk,
~15 parallel sonnet verifiers), scoring PASS / FAIL / REGRESSION / UNCLEAR —
never PASS without having driven the steps.

First pass found 12 not simply PASS:

| surface | id | verdict | what was still wrong |
|---|---|---|---|
| chipstatus | F-13 | FAIL | 2Q gate table's Length column still printed the raw `#./inferred_length` pointer for 16/21 rows |
| chipstatus | F-20 | FAIL | Back did not restore the metric-section scroll offset |
| regenerate | regenerate-r2-18 | FAIL | a second build's recipe still overwrote the first chip's scripts folder |
| jsontree | JT-14 | FAIL | Esc did not close the type picker opened from its own gear button |
| jsontree | jsontree-r2-23 | FAIL | an open Config Manual moved off the panel a row's + opened |
| liveedit | F14 | FAIL | an Auto-Sync flush answered before the live write actually landed |
| liveedit | F17 | FAIL | Back from Flat View did not return to Table View |
| liveedit | liveedit-r2-02 | UNCLEAR | harness could not reliably reach Flat View's search box right after a reload (settle-timing race, not confirmed as a product bug) |
| liveedit | liveedit-r2-09 | FAIL | an idle second window kept showing a value a lab-mate had already undone on the live chip, 25 s later |
| datasets | datasets-r2-15 | FAIL | Enter in the Prev State run box reopened the run instead of committing |
| datasets | datasets-r2-20 | FAIL | a `data.json` rewritten after the scan was still named unreadable |
| diagnostics | diagnostics-r2-11 | UNCLEAR | an open page showed only the pill, not the "live chip changed" banner |

Ten landed dedicated follow-up commits the same day: `335bace` (F-13),
`26a6eab` (F-20), `bd53c5a` (regenerate-r2-18), `8cde942` (JT-14), `ea278d7`
(jsontree-r2-23), `87b11d0` (liveedit F14), `427f307` (liveedit F17),
`d33e8db` (datasets-r2-15), `5a05a03` (datasets-r2-20), `986fd19`
(diagnostics-r2-11). The two remaining liveedit items stayed open at the
close of this pass: `liveedit-r2-02`'s harness race was never confirmed as a
real defect, and `liveedit-r2-09`'s cross-window staleness needed the
sync-status rework (docs/209) rather than a point fix.

The closing tally of the full 330-item re-verification: **319 PASS, 8 FAIL,
3 UNCLEAR**.

## 4. Pins

Each of the ten follow-up commits carries its own pin (mutation-checked
individually — see each commit message). The findings themselves live in
`D:\work\sm_qa_rigs\_findings\all_findings.json` (335 raised, 330 confirmed),
`reverify_all.json` (the 330 re-verified + what their fix package claimed),
`reverify_fails_partial.json` + `reverify_fails_rest.json` (the 12 non-PASS
verdicts with evidence).
