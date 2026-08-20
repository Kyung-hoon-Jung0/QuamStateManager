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

## Notes for later

- The per-row Diff is read-only by design. If a "pull just this value"
  per-row action is ever wanted there, the review modal's ✓ →
  `/field/edit-batch` path is target-agnostic and could be lifted — but it
  must come with the same edited-paths marking the sync modal carries.
- `diff_n`'s adjacent-column `changed` uses base-vs-i for the row verdict and
  i-1-vs-i for cells; with the 1e-12 relative tolerance these can in theory
  disagree on pathological chains — accepted, same as `diff`'s own tolerance
  semantics.
- Snapshot ts ordering relies on `_ts_stamp`'s truncated-microseconds suffix
  (lexicographic = chronological for modern stamps; bare v1 stamps sort first
  within their second).
