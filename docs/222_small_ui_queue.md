# docs/222 — Small UI queue: "Live edit - Json Tree view", no bare arrow on Chip Status titles, a slim loader strip, a compact run list by default

2026-09-26/27, branch `w7/smallui` (4 commits), merged `16e3acd` into
`integ/w7`; three follow-ups on `integ/w7` (`6541800`, `13458ea`, `8656f2b`).
Queue items #2, #5, #7, #8.

## 1. What was measured

Independent verifiers, real headless Chrome, branch vs base (krs5 unless
noted; loader sizes are verifier 2's bench group `smallui`, median of 5):

| | base | branch |
|---|---|---|
| #7 loader gap below the top bar (krs5 and big30x) | 347 px | 4 px |
| #7 loader height (krs5 and big30x) | 147 px | 33 px |
| #7 loader width (krs5) | 503 px | 441 px |
| #5 panel titles / Overview tiles carrying an arrow (krs5) | 7 / 4 | 0 / 0 |
| #5 `.metric-dir` arrows without hover text | 15 | 0 |
| private window, SecurityErrors from `_applyPersistedHeight` walking /datasets, /bulk, /explorer, /topology (verifier 2) | 28 | 0 |
| private window, exceptions on /datasets + two htmx swaps (verifier 1; the one left also happens on base) | 12 | 1 |

- #7 at 1600: strip top 58 px vs top bar bottom 54 px, 33x441 px, one line
  (implementer and both verifiers agree). At 1366: 56 vs 52 px, 31x421 px
  (verifier 2). At 390x844: top 45 px under a 41 px top bar, 25 px high; with
  the top bar hidden (`--topbar-height` 0px) the strip sits at 4 px (verifier 1).
  Shown during genuinely slow requests on big30x (/bulk 26-31 s, /topology
  8-10 s, /diff 3-7 s), never stuck after settle, 0 frames during Trends chip
  toggles (`/topology/trends` is excluded in `isSlow` by design).
- #5 on big30x: 115 panels + 15 tiles, 0 arrows (verifier 2).
- #8: absent, junk, empty or `'1'` key -> compact, 39 rows all 26 px; `'0'`
  -> full rows, max 52 px (implementer and verifier 2, krs5 and big30x, 1366
  and 1600). Verifier 1 measured the compact rows at 31 px with every date
  group opened.
- Latency: the bench's small ms differences flip direction between chips
  (e.g. `nav.jsontree_click_ms` 345 vs 329 on krs5, 2419 vs 2848 on big30x,
  branch vs base); the verifier attributes no regression to the branch, which
  touches no hot path.
- The branch journey `smallui.cjs` gives 25/25 on the branch and 12/25 against
  the base (verifier 1): every #2/#5/#7/#8 claim fails on the base.

## 2. The four items (`167c3bd`, `fb14a1b`, `d94e682`, `89f4533`)

- **#2** (`167c3bd`): the sidebar sub-item under Live State Edit and the
  command-palette entry read exactly "Live edit - Json Tree view". Route
  `/explorer` and page token `explorer` are unchanged.
- **#5** (`167c3bd`, `d94e682`): the arrow beside every Chip Status panel title
  and Overview tile title was never a control. It was `META.direction`, the
  metric's good direction, drawn as a lone glyph, so it read as a dead button.
  Those titles no longer draw it; the direction moves into the title's hover
  text in words ("... Higher is better." / "(lower is better)"), not repeated
  when the blurb already says it (`d94e682`). Arrows kept on compact cards and
  in the threshold editor (where the direction decides which side of the fail
  line is bad) carry a tooltip, an aria-label and a help cursor.
- **#7** (`fb14a1b`, `d94e682`): `#quam-loader` is one slim strip pinned at
  `calc(--topbar-height + 4px)`: ring spinner, "Please wait a moment... a first
  open can take a while.", and the Param History import count when an import
  runs; `pointer-events: none`. The letter-by-letter QUAM STATE MANAGER card is
  gone. The docs/146 rules are unchanged (80 ms delay gate, cleared at the slow
  request's own afterSettle). `fb14a1b` added an elapsed counter;
  `d94e682` removed it as a duplicate of the top bar's NavProgress right above.
- **#8** (`167c3bd`): an absent `quam_exp_list_compact` now means compact.
  `base.html` renders `body.exp-list-compact` server-side, so a first visit
  never flashes full names; only an explicit `'0'` (Full names) wraps. The
  toggle is kept; a junk value or a private window gives compact. Side fix: the
  json-panel height restore no longer throws on every htmx swap when storage
  is blocked (try/catch around the read).
- `89f4533`: the journey opens the date groups and scrolls the run list into
  view before each #8 shot (its shots had shown the Datasets table).

## 3. Independent verification

- Round 1: PASS, no defects. Staleness: an outside write of q1.T1 showed the
  same T1 panel after an htmx navigation as after a cold reload, plus the drift
  banner.
- Round 2 (krs5 and big30x, 1366 and 1600): PASS, two P3s and two nits.
  - **P3a — the json-panel side fix had no pin.** Removing the try/catch kept
    every pin green. Fixed on `integ/w7` by `13458ea`:
    `tests/json_panel_height_selfcheck.cjs` drives the REAL `app.js` under
    jsdom (a stored height is applied at load and to a swapped-in panel, so the
    negative checks cannot pass vacuously; a throwing localStorage getter
    raises nothing from DOMContentLoaded or htmx:afterSwap) +
    `tests/test_json_panel_height.py`. Try/catch removed -> red.
  - **P3b — reverse flash for Full-names users.** With the key `'0'`, the
    server-rendered compact class was removed only at DOMContentLoaded: on
    krs5 at 1366x900, first paint 76 ms, DCL 107 ms, so compact rows were
    painted about 30 ms before reflowing to full (on big30x first paint 84 ms,
    DCL 80 ms, no flash measured). Fixed on `integ/w7` by `8656f2b`: a one-line
    inline script, first in the body, drops the class when localStorage holds
    `'0'` (try/catch). The sources record pins and mutations for it, not a
    real-Chrome re-measurement.
  - Nits (not fixed): the strip sits over the page's own header during a slow
    load (at 1366 on /topology it covers the "Panels" button label; about 31 s
    on big30x /bulk; clicks pass through); `#quam-loader` got `role=status` but
    keeps `aria-hidden=true` while visible (the base had aria-hidden too).

## 4. Integration

- `tests/test_topbar_twerk.py::TestTheShellCannotOverflow::test_the_shell_body_carries_the_class`
  required `<body class="app-shell">` exactly and went red on `w7/smallui`
  alone and on integ; `6541800` makes it require the class list to START with
  `app-shell`.
- The csmeta merge conflicted in `chip-status.js`: smallui's rule that no Chip
  Status title carries a bare arrow (`labelHtml(..., true)`) was kept and
  subsumes csmeta's per-tile `noArrow`; see docs/224.

## 5. Pins

- `tests/exp_list_compact_selfcheck.cjs` (10) + `tests/test_exp_list_compact.py`
  — `expListCompactPref` changed to `=== '1'`: red (3 failures per the
  implementer and verifier 2, 2 per verifier 1); `body.exp-list-compact`
  removed from `base.html`: selfcheck 2 failures, pytest 2 failed. `8656f2b`
  adds a server-side pin (first element after `<body>`, key, value, try/catch)
  and runs the exact inline script alone for `'0'` / absent / `'1'` / throwing
  storage; script removed, try/catch removed, wrong key value: all red.
- `tests/chip_status_qa4_selfcheck.cjs` (46) — panel title arrow restored,
  tile title arrow restored, hover title removed from a kept `.metric-dir`:
  each red.
- `tests/loader_selfcheck.cjs` (35) — loader back to `top: 50%`: red.
- #2: `tests/test_web.py::TestSidebarIAr15::test_group_memberships` and
  `tests/test_pulses_routes.py::TestPulsesLibrary::test_pulses_nested_under_live_edit_nav`
  — label reverted: both red.
- `tests/json_panel_height_selfcheck.cjs` + `tests/test_json_panel_height.py`
  (`13458ea`, above).
- `tests/browser/journeys/smallui.cjs` — 25 real-Chrome checks.
- cqt: `test_exp_list_compact`, `test_pulses_routes`, `test_web`,
  `test_sidebar_tools`: 719 passed, 19 skipped (implementer and verifier 2).
  `npm run selfcheck`: 207 passed (implementer, verifier 1); 206/207 for
  verifier 2, the failure being the pre-existing `chip_status_resume`
  wall-clock flake (branch 2/10, base 4/10). The full suite was not run on the
  branch; the integ full suite caught the topbar pin above.

## 6. Open / not done

- About 15 other user-visible texts still say "Json Tree View" (e.g. the "Show
  in Json Tree View" buttons in `_type_alarm_banner.html` and
  `_diagnostics_types.html`, the landing page's "Browse in Json Tree View").
  The request named only the sidebar sub-item; whether the wording spreads is
  the user's call.
- #7: no elapsed counter inside the strip (judgment call; a small re-add).
- The two round-2 nits above.
- Pre-existing, not from this branch: `bulk-edit.js` `_readScale` still throws
  a SecurityError on /bulk in a private window; `htmx:historyCacheError` is
  logged on history.back.
