# 97 — Drift/conflict popup: one directional trio + a diff you can actually see

*2026-08-09. A UI/UX pass on the state drift/conflict surfaces, prompted by two user
reports. No backend, route, or handler change — every action already existed. Labels are
unified into a directional trio, and the per-field Δ (the whole point of the popup) is made
prominent. SM-additive: no behaviour changes for any chip, only button text + scoped CSS.*

## The two reports

1. **"There's no *just pull live and discard my edits* option."** There was — on every
   surface — but it was labeled **six different ways**: `Discard` · `Sync` ·
   `Pull live state` · `Discard & pull live` · `Pull latest & discard mine` ·
   `Pull & discard`. In the branch a user usually hits (Review modal, unsaved edits) it was
   the single word **"Discard"**, which never read as the symmetric opposite of the
   well-labeled **"↑ Keep mine — overwrite live"**. So users concluded the choice was missing.
2. **"The diff in the popup is too small to notice."** Correct: the Δ fell back to the base
   `.val-delta` (~**10.25px** = 0.82em of a 12.5px row), sat **last** in a wrapping flex row,
   had its percentage dimmed to `opacity:0.78`, followed a muted-gray "before", and there was
   **no `.review-delta` rule at all** (the class was passed but never styled). The one thing
   the popup exists to show was the least visible element on the row.

## Fix 1 — one directional trio, everywhere

The three resolution actions now carry the **same directional labels** on every surface, so
"take live / keep mine / merge" read as an obvious, symmetric set. Handlers are UNCHANGED.

| Label | Handler (unchanged) | Direction |
|---|---|---|
| **↓ Take live** — discard my edits | `doStateSync('discard')` (`mode=discard`) | working ← live (my edits dropped) |
| **↑ Keep mine** — overwrite live | `overwriteLiveWithWorking()` / `apply-to-live?force=1` | working → live |
| **⇄ Pull & apply** (merge) | `doStateSync('apply')` (`mode=apply`) | pull live, replay my edits, push |
| Re-apply (review first) | `doStateSync('reapply')` (`mode=reapply`) | pull live, re-stage for review |

The trailing qualifier ("— discard my edits", "(merge)") is a muted `.sync-btn-sub` span so
the direction reads first and the detail second. The Review modal's body note now spells the
three directions out in prose (there is vertical room there); the space-constrained tray and
banner carry the outcome in the `title=` tooltip.

Surfaces touched (labels only): `templates/_state_review.html` (all three branches —
`review-sync-edits` / `review-sync-saved` / `review-sync-clean`),
`templates/_state_apply_conflict.html` (both the `_staged` and pending-stash branches),
`templates/_live_diverged_banner.html`.

**Not changed:** every `onclick`/`hx-post` target, `doStateSync` mode, `?force=1`, the
`review-sync-*` reveal spans (`reviewAccept`/`_reviewRevealEditSync`), `review-delta`
(`reviewDeltaWatch`), `tray-force-btn`, `state-review-overwrite-btn`, the hidden-branch logic,
the archive gating, and the load-bearing `confirm()`s (docs/65 double-prompt guard). The
`_staged` conflict branch still deliberately omits the merge option (nothing to replay).

## Fix 2 — the Δ is the headline (moderate), scoped to `.state-review`

CSS-only in `web/static/style.css`, every rule under `.state-review` so the pending tray,
Param/State History, `/diff` and `/compare` Δ (dense surfaces, docs/76) stay byte-identical:

- **New `.state-review .review-delta.val-delta`** — the Δ becomes a real chip: **14px**,
  `font-weight:700`, a tinted background + border keyed by `.delta-up`/`.delta-down`/`.delta-same`
  on the existing `--diff-delta-pos-color`/`--diff-delta-neg-color` tokens; `.val-delta-pct`
  lifted to `opacity:0.9`.
- **Value line enlarged** — `.review-row-vals` 12.5px→14px, `.review-live-input` 12.5px→14px,
  `--review-ctl-h` 22px→24px, `.state-review-cards` 13px→14px.
- **Transition reads** — `.review-old` de-muted (82% of `--pico-color`), `.review-arrow`
  12px→15px and bolder.
- Nothing in `core/value_delta.py`, the `value_delta` filter, `_delta_macros.html`, or
  `window.ValueDelta` changed — the docs/76 char-for-char parity (`tests/test_value_delta.py`)
  is untouched. The fixed px scale (deliberately decoupled from `--bulk-fs`, docs/42) was
  raised, not re-coupled.

## Tests

Label pins updated to the new vocabulary (intent preserved): `tests/test_overwrite_live.py`
(banner offers both directions → "Take live"), `tests/test_review_scroll.py` (compact header
pull button), `tests/test_state_roundtrip.py` (staged conflict tray shows overwrite, omits
merge). `tests/state_sync_selfcheck.cjs` pins the `doStateSync` **modes**, not the labels, so
it is unchanged. No new behaviour to pin — this is a presentation change.
