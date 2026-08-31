# docs/147 — the diff workbench: side-by-side figures, a named direction, a top-anchored tab (2026-09-01, customer round)

Three items from live customer use of `/diff` (Compare selected).

## ① Figures pair side by side even across DIFFERENT experiments

The figures tab paired rows by NAME only (docs/84-era design) — correct for
two runs of the same experiment, but comparing two different experiments
produced disjoint name sets, so A's figures rendered as a column followed by
B's below it. Side by side is the entire point of pressing Compare
(user-stated, emphatically). Pairing now: a name shared by ≥2 sources still
pairs BY NAME (the same-experiment case keeps its honest blanks — an absent
figure is a blank, never a substitute); every REMAINING figure pairs
POSITIONALLY, i-th leftover beside i-th leftover, and a mixed row captions
each cell with its own file name (`.diff-fig-cellname`) so nothing pretends
to match. Names are folder-sorted per source (`_diff_run_figures`), so "the
i-th figure" is deterministic. Verified against the real archive first:
same-experiment runs share names exactly (three experiment families
probed), so the name-matched half genuinely covers that case.

## ② The 2-way verdict row names its direction

The 3-way pane view marks its BASELINE loudly; the 2-way list/tree/node/data
pages showed `7 changed +32 added…` with no hint which way `old → new`
reads. An identity-blue badge (`.diff-dir-badge`, a hair larger than the
counts) now leads the counts row: **A #256 → B #255** — run ids when the
labels carry them (`_diff_short_label`: first `#\d+`, else front-truncated
at 18 chars), full labels in the tooltip. 3+ sources keep the pane baseline
marker instead (a 2-sided badge has no meaning there).

## ③ A tab or source change starts at the top

The tab buttons swap `#diff-root` while the scroll container (`#table-pane`)
keeps its scrollTop — a longer tab (data) opened mid- or bottom-scrolled.
The workbench's inline script now keys `tab|a|b|n` on `window.__diffScrollKey`
and resets the nearest scrollable ancestor to top when the key CHANGES;
a base-only or view-only change keeps the scroll (the user is mid-table
when re-basing — `base` is deliberately not in the key).

Pinned by `tests/test_diff_three_way.py` — `TestFiguresTab` (name-matched +
positional counts, per-cell captions, the disjoint-experiments case
asserting A's first and B's first figure share one row) and
`TestDirectionBadgeAndScroll` (badge on 2-way, absent on 3-way panes,
`_diff_short_label` contract, scroll-reset script with `base` excluded from
the key). Mutations 4/4 red: positional pairing removed (the stacked
columns return), captions dropped, badge removed, scroll reset removed.
