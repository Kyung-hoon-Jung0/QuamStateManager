# 128 — Versions panel: per-row Diff, differences-only N-way Compare, compact rows

**Date**: 2026-08-21 · **Trigger**: customer feedback on the docs/126 Versions
panel (three items, one session) · **Pinned by**: `tests/test_state_versions.py`
(`TestVersionDiff`, `TestVersionsCompareNway`, `TestCompactRows`)

The docs/126 Versions panel listed WHEN each version landed, but answering
WHAT a version holds cost tick-two + Compare — a navigation. The customer
asked for three things:

1. **A Diff button on every row**, left of Pull to Live, opening a floating
   this-version-vs-now comparison — "the sync popup could almost be reused
   as-is".
2. **Compare kept, but landing on the answer**: picking 3 versions and
   pressing Compare must IMMEDIATELY show only the keys whose values differ,
   one column per version — not the Compare hub's configuration surface, and
   never rows whose values agree ("comparison is meaningful in the
   differences").
3. **Compact rows** — most of the 46rem panel width was the gap between the
   date column and the right-aligned actions.

## ① Per-row Diff — the sync review modal, minus its writes

The customer's reuse instinct was right, with one amendment: the sync review
modal's **data pipeline and shell are generic, its actions are not**.

- **Engine**: the comparison already existed — `HistoryManager.diff_current`
  (history.py) runs `Differ.diff(snapshot_dir, current_store)` against the
  in-memory store, so the live files are never opened (docs/28). The new
  route `GET /state/versions/<ts>/diff` (routes.py) is a thin caller:
  `_HIST_TS_RE` shape gate pre-join (a `..\..`-shaped ts escapes the history
  root on Windows), 300-entry cap with a visible "showing X of Y", 404 on a
  malformed ts, `_status.html` on a missing snapshot — never a 500.
- **Shell**: a third `#version-diff-overlay` in base.html reusing the
  `.state-review-overlay/-backdrop/-host` classes (already shared by the
  live-drift overlay). `StateVersions.diff(ts)` / `closeDiff()` mirror
  `openReview`'s fetch-inject-trapFocus pattern.
- **Rows**: `_version_diff.html` reuses the review modal's row language
  (`.review-row`, M/A/D gutter, `dot-path`, `groupdigits`, `delta_chip` —
  the docs/76 single Δ implementation). Old side = the version, new side =
  now, forward in time.
- **What was deliberately NOT reused**: the directional trio, per-row ✓
  accept, `doStateSync`, `/state/apply-to-live` — all sync-flow writes. The
  panel is read-only; **Pull to Live stays the one gated write**, and the
  read-only preview beside it makes that press an informed act.
- **Honesty**: with unapplied edits, a note states the "now" side is the
  working state, not the live chip. "Full view" links the docs/84 workbench
  with the same `hist:`/`working:` ref grammar the tick-two flow uses.
- The panel's click-away closer now ignores clicks inside
  `#version-diff-overlay` (OUR overlay only — the sync-review/live-drift
  overlays keep their pre-existing dismiss behavior, since their actions can
  stale the panel's content), so closing the diff lands back on the row.
- Diff is offered on EVERY row — archives and the current row included
  (comparing is always safe where restoring is not).

## ② N-way Compare lands on the differences

3+ ticks used to open the Compare hub — a surface that asks for context,
tolerance and mapping before showing anything. New landing:
`GET /diff/versions?ts=…&ts=…` → `_version_compare.html`, a plain table:
one row per differing dot-path, one column per version (oldest → newest,
docs/76), agreeing leaves never rendered.

- **Engine**: `Differ.diff_n(sides)` (core/differ.py) — every leaf
  (state + wiring via `_flatten_side`, unlike `multi_diff`, which walks only
  curated qubit properties), `diff`'s own equality (same float tolerance,
  same `__class__` ignore). Each row carries a per-side `changed` verdict
  ("differs from the column before it") computed with the SAME equality as
  the row verdict — the docs/118 two-rules trap, avoided by construction.
  The template's `.vc-changed` cell tint (the shared `--diff-cell-bg/border`
  tokens) and Δ chips key off that verdict, never a second comparison.
- **Caps, honest**: 1000 rows / 8 columns (`_VERSION_COMPARE_ROW_CAP/_COL_CAP`),
  each tripped cap rendering a visible note (newest columns win).
- **Gates**: ts values are `_HIST_TS_RE`-filtered pre-join; <2 valid → 302 to
  /diff; a `chip_key` naming a different chip than the open one is refused
  honestly ("open that chip first") instead of answering from the wrong
  device's history; a missing snapshot explains instead of 500ing.
- **The hub survives** as the "Advanced: open in Compare hub" link on the new
  page (entity mapping across devices is still its job); 2 ticks still open
  the /diff workbench. `StateVersions.compare` and the pick-hint were updated.

## ③ Compact rows

`.state-version-panel` width 46rem → 36rem (the quick-diff table stays
readable; ~26rem of row content no longer floats in 20rem of gap), and
`.sv-row-actions` became a flex cluster (`gap: .3rem`) for the two buttons.
The `_clampToViewport` comment was updated to match.

## Review round (same day) — 9 confirmed findings, all fixed

An adversarial 3-lens review (correctness / house contracts / regression)
with per-finding refutation confirmed 1 major + 8 minor (one duplicate pair):

1. **MAJOR — `smModalOpen` didn't know the new overlay**: every
   global-shortcut gate keys off `window.smModalOpen()`, so j/k/Enter run
   navigation and `?` kept firing BEHIND the version-diff modal (Enter
   swapped `#table-pane` underneath it; the cheat sheet opened at z-5000
   under the z-9600 overlay, invisible). `#version-diff-overlay` added to
   its selector.
2. **Chip-identity gate**: two windows share one server context (docs/120);
   a Diff press racing a chip switch answered from the wrong chip's history
   with an internals-leaking "Diff failed: …". The button now ships
   `chip_key`; a mismatch is refused with "open that chip first" (the same
   gate `/diff/versions` carries).
3. **Stale-response race**: `StateVersions.diff()` had no in-flight token —
   a slow cold-snapshot response could repaint over a newer row's (or a
   closed overlay's) content, the docs/122 class. `_diffGen` monotonic token,
   checked in both handlers, bumped on close.
4. **Bare-toast error pages**: `/diff/versions` error paths rendered
   `_status.html` — a chrome-less fragment — on full-page reloads of the
   pushed URL. Both paths now render the version-compare template (full page
   or partial by `_is_htmx()`) with an `error` block.
5. **Stored null rendered blank**: `groupdigits(None)` is `''`, so a
   null-vs-value row showed an empty cell — the difference invisible,
   indistinguishable from `""`. Stored null now renders an italic `null`
   (`.vc-null`), distinct from the absent-leaf en-dash.
6. **The Pull-to-Live sentence where no such button exists**: the overlay's
   note instructed a press the panel deliberately withholds on archives and
   the current row. Gated by `offers_pull` computed server-side.
7. **Click-away guard scope**: narrowed from `.state-review-overlay` to
   `#version-diff-overlay` (see above).
8. **`diff_n` docstring overclaim**: base-vs-each row verdict + adjacent
   `changed` means a sub-tolerance chain (a≈b, b≈c, a≉c at 1e-12) can list
   a row with no marked cell; the docstring now states the caveat instead
   of claiming the impossible.

Each fix is pinned in `TestVersionDiff`/`TestVersionsCompareNway`.

## Heavy review, round 2 (2026-08-21, after ce73f2b shipped)

A 41-agent red team ran against the SHIPPED commit in an isolated worktree —
six lenses (claim audit / test quality / runtime execution / security /
UX-honesty / integration), every raised finding independently refuted by a
second agent that had to demonstrate the failure, then a completeness critic
over the whole thing. **34 findings raised, 4 confirmed, 30 refuted**, plus 1
new finding from the critic. Notably, the entire security lens was refuted
(the `onclick` attribute injection is closed upstream by `_sanitize_name`'s
`[^\w\-.]` substitution on `chip_key` and `_HIST_TS_RE` on the timestamp),
and 15 of the 15 test-quality findings were refuted on execution — but the
one that mattered was raised by the critic, below.

**① The "compact" change was a regression, measured.** (critic, major)
Narrowing `.state-version-panel` 46rem→36rem — item ③ of the original
change — crushed the docs/126 quick-diff table that shares that width: on the
real CQT 20Q chip a 48-char path fell from 24 chars/2 lines to **4.8
chars/10 lines**, and the table grew 2,040px → 7,512px, pushing the version
list itself ~12 screens down. The CSS comment claimed the opposite. **The gap
the customer complained about was never the width** — it was `margin-left:
auto` on `.sv-row-actions` across a growing `.sv-meta`, which pins the buttons
to the far edge at ANY width. So the width is restored and the ROW is
compacted instead: the actions now render between the date and the meta (in
markup, so tab order matches the eye), and `.sv-meta` absorbs the slack at the
right where nothing is being read. `.sv-q-path` also gained a `min-width`
floor so the quick-diff cannot be crushed again at a narrow viewport.

**② The N-way key column collapsed to one character per line.** (major)
`overflow-wrap: anywhere` gives `.vc-key-col` a ONE-character min-content and
`.vc-val { white-space: nowrap }` makes every value column incompressible, so
auto table layout handed the key column its minimum the moment the values
stopped fitting: measured 44px wide with 337px-tall rows at 4 columns on a
1280px laptop — i.e. the FIRST case past the surface's own "3+ ticks" minimum.
Fixed with `min-width: 18rem`; the table is allowed to be wider than the
viewport, and `.vc-scroll` scrolls it.

**③ The sticky headers never stuck.** (minor) `.vc-scroll` had `overflow-x:
auto` and no height bound, so it never overflowed vertically — meaning it was
the sticky containing block but not a scrollport, the real scroller being
`#table-pane`. Column headers left the screen after the first screenful of a
240-row diff, and the horizontal scrollbar sat at the bottom of a 131,602px
box. Fixed with `overflow: auto; max-height: calc(100vh - 185px)` — the same
pattern `.bulk-table-wrap` uses, which is why that table's headers do stick.

**④ The overlay never said which version you were looking at.** (minor)
`.ts-local` ships `visibility: hidden` until `applyLocalTimes` stamps
`data-localized`, and that runs only on `htmx:afterSwap`/DOMContentLoaded. The
overlay is injected by a raw `fetch` + `innerHTML`, so the ONE line naming the
snapshot rendered as a 373px blank gap. One line added after injection. The
sibling `_loadDriftView` carried the identical pre-existing defect (its
"baseline: <ts>" line) and is fixed in the same way.

**⑤ Two absolute sentences were false.** (minor) The page prints "only keys
whose values differ are listed — every other leaf agrees across all N" and
"identical content on every leaf", while `diff_n` inherited `Differ.diff`'s
`__class__` ignore — so a lab's class migration (docs/94, a real event) read
as "no differences", *while the 2-tick button on the same panel reported it*.
Two buttons, one pair of versions, opposite answers. Both new routes now pass
`ignore_keys=set()` (`HistoryManager.diff_current` gained the passthrough), so
a migration is visible and the surfaces agree. The critic also re-opened the
refuted NaN finding as the same defect's other half: IEEE `NaN != NaN` listed
a leaf as "differing" whose cells both read `nan`. `diff_n` now treats two
NaNs as agreeing, following the rule docs/118 already settled in
`compare_equal` rather than inventing a third answer.

**What the review said about the tests, and what was done.** Every JS-side
fix from round 1 was pinned by grepping app.js for a string — the exact shape
this repo has a scar about (`TestBookmarkMoved`'s docstring records a pin that
stayed green while the code containing it could not execute). The token could
have been renamed to a no-op with every test still passing. `tests/
version_diff_selfcheck.cjs` now drives the real shipped app.js under jsdom
(23 assertions): the request URL and its chip_key gate, the focus trap, the
timestamp actually being revealed, `smModalOpen` reporting the overlay, the
away guard keeping the panel for OUR overlay only, the stale-response token
under a deliberately inverted response order, a response landing after close,
and the 2-vs-3+ Compare routing split. Mutation-checked: removing the
`applyLocalTimes` call or neutering the `_diffGen` guard each turn it red.

### The five fixes, measured (not asserted)

All five were then measured in real headless Chrome against a COPY of the
real CQT 20-qubit customer chip (2,060 numeric leaves, real 64–68-char
dot-paths, 58 minted versions), at 1280/1600/1920 in BOTH themes, with each
"before" produced by neutralising only the fixed declaration. Every number
below is light-theme-identical to dark.

| fix | before | after |
|---|---|---|
| `.vc-key-col` min-width | 44.1px wide, 64 line boxes, 1.0 char/line, rows 922px | **360–378px**, 2 lines, 32 chars/line, rows 58–62px |
| `.vc-scroll` scrollport | `clientH == scrollH` (not a scroller); `<th>` scrolled away 1500px | **715 / 51,619**; `<th>` displacement **0.0px** against 1500px of row movement |
| panel width + `.sv-q-path` floor | path col 160.5px, 5 lines, 13.6 chars/line, table 791px | **370.5px**, 2 lines, 34 chars/line, table 451px |
| row gap (actions after date) | **546.8–685.9px**, Diff-button x spread 102px | **10.0/10.5px** — exactly the row's own `gap`, identical on all 40 rows; spread 9.4px |
| overlay timestamp | `visibility: hidden`, `h3.innerText == "Version  → now"` | `visible`, `"Version 8/21/2026, 12:57:22 PM → now"` |

The measurement round then found two NEW problems, both of which are the
fixes' own trade-offs rather than pre-existing conditions:

- **The gutter moved rather than went away.** Restoring 46rem doubled the dead
  space *inside* the quick-diff table (path text → value: 131.8px → 251.6px
  @1280, 138.4 → 264.4 @1600/1920) — the customer's original complaint,
  relocated one element up. `.sv-quick-table` is now `width: auto;
  max-width: 100%`, which re-measured as **−126.0px (−48%) on short paths and
  exactly 0.0px on long ones** — a partial fix, and the long-path case is the
  one that motivated the `.sv-q-path` floor in the first place. The remaining
  gutter is not the table's width: it is ONE shared column sized by the
  longest path (221.9px of empty *path cell* on a short-path row) plus
  right-aligned nowrap values. Closing it properly means abandoning the
  two-column table for the stacked path-over-value row `_state_review.html`
  uses — deliberately not attempted here. Nothing regressed: column
  alignment, the 12rem floor, panel overflow and table height are all
  unchanged (12 combinations, both themes).
- **`calc(100vh - 185px)` under-counts the real chrome.** `.vc-scroll`'s top
  is actually 339.8px @1920 / 429.2 @1600 / 465.8 @1280 (the topbar wraps), so
  the scroller's bottom edge — where the horizontal scrollbar is drawn —
  started 155–281px below the fold. Reachable (one outer scroll), and an
  enormous improvement on the 51,185px it replaced, but not visible at first
  paint. `.version-compare` is now a flex column with `height: 100%` and
  `.vc-scroll` `flex: 1 1 auto`, so the pane sizes it exactly where that
  resolves, with the calc kept as the fallback. Measured after: the chain
  does resolve (`.version-compare` height == the pane's content height at all
  six viewports), the overshoot went **+280.8px → −22.0…−23.4px** (inside the
  viewport at first paint, no scrolling), the calc never binds, and the
  document stopped scrolling vertically at all.

  **That fix then shipped a worse bug than the one it fixed, and the same
  measurement round caught it.** Written with the reflex `min-height: 0`, the
  flex item collapsed to **0.0px** once `#table-pane` was dragged to ≤25% —
  and that pane is explicitly draggable (its own CSS comment: the inspector
  may cover the page). The heading and the "N differing keys" line rendered
  over an empty void where 892 rows belonged, **unrecoverable by scrolling**;
  only dragging the gutter back restored it. `min-height: 8rem` is the fix:
  it converts "content annihilated" into an ordinary overflow the pane
  scrolls. Re-measured: `allRowsReachable` **False → True** in the four
  configs that had failed, the last of 892 rows rendering 57.6–61.2px tall at
  every pane size, and the floor is provably **inert** in normal use
  (`minHeightBinding: False` at all six viewports — every number from the
  previous round reproduced unchanged). 8rem was chosen by measuring 8/10/12/
  16rem: rows-visible-at-rest is identical at every value (so a bigger floor
  fixes nothing), and 12rem is non-monotonic — it shows *fewer* rows after
  scrolling than 10rem, because a floor taller than the pane hangs further
  below the fold. Residual, stated plainly: at the tightest drag (105px pane,
  84px heading) 0 rows are visible at rest — inherent to the heading filling
  the pane, unaffected by the floor value, and now recoverable by scrolling.

**Known limitations, bounded, measured, not fixed:**

- With a large quick-diff (9+ changed leaves, table ~451px in a 663px panel)
  the version list itself is pushed below the panel fold — 1 of 40 rows
  visible at 1280, 0 at 1600/1920. In the ordinary case (the 3-entry diff a
  normal apply produces) it is **7 of 40 @1280, 6 of 40 @1600·1920**. The
  panel scrolls, so nothing is unreachable. The real answer is probably a
  collapsed-by-default quick-diff on large diffs; not attempted.
- The long-path gutter inside the quick-diff table (above): shared-column
  sizing, needs a layout change, not a width change.
- At a 105px `#table-pane` (extreme gutter drag) the N-way table shows 0 rows
  at rest, and `.vc-note` is scrolled out. Reachable by scrolling.

A methodological note worth keeping: **three of the four defects in this
section were introduced by fixes for the defect above them.** Item ③'s
narrowing broke the quick-diff; the flex chain that fixed the scrollbar
annihilated the table under a drag; the `width: auto` that was supposed to
close the gutter is a no-op where the gutter is worst. Each was caught only
by measuring the *rendered result on a real chip*, never by reading the CSS
or by the test suite — every one of these shipped green.

**Stated as unverified** (the critic's list, kept honest rather than padded):
customer acceptance of the three surfaces; behaviour under a real concurrent
writer (a qualibrate node rewriting state.json mid-press); two real SM windows
on one server context — the chip_key gate was only ever exercised by forging
its argument; screen-reader and forced-colors behaviour; whether the trimmed
CLAUDE.md's ~40 surviving contract claims are still accurate. Explicitly NOT a
caveat: hardware, an OPX, or an API key — this change set touches no autofit
or instrument path.

## Notes for later

- The per-row Diff is read-only by design. If a "pull just this value"
  per-row action is ever wanted there, the review modal's ✓ →
  `/field/edit-batch` path is target-agnostic and could be lifted — but it
  must come with the same edited-paths marking the sync modal carries.
- `diff_n`'s adjacent-column `changed` uses base-vs-i for the row verdict and
  i-1-vs-i for cells; with the 1e-12 relative tolerance these can in theory
  disagree on pathological chains — accepted, same as `diff`'s own tolerance
  semantics.
- `diff_n` now departs from `Differ.diff` in exactly one way (two NaNs agree)
  and its two callers depart in one more (`ignore_keys=set()`). Both are
  deliberate and documented at the call sites; a third caller should decide
  consciously rather than inherit.
- The critic listed gaps no lens covered that are still open: nobody pressed
  the real Compare button (the pick hint says "Lists what differs across all
  N" even when N exceeds the 8-column cap, and browser Back after a Compare
  press leaves the comparison in the pane while the URL reverts — both
  PRE-EXISTING, neither this change's), archive/read-only chips were never
  loaded and Diff pressed on them, and container-shaped leaves in `diff_n`
  (a list going `[1,2] → []`, or an insertion renumbering every `x.i` row)
  were never constructed.
- Snapshot ts ordering relies on `_ts_stamp`'s truncated-microseconds suffix
  (lexicographic = chronological for modern stamps; bare v1 stamps sort first
  within their second).
