# 74 — Run-parameter comparison + sidebar run-id search (r16 ⑧⑨, 2026-08-03)

Branch `feat/ds-compare-params-search`.

## ⑧ Compare selected → full run-parameter table

The dataset Compare view's Parameters tab used `Differ.compare_parameters`'
differences-only output — users wanted the WHOLE parameter picture at a
glance. `compare_parameters` gains `include_equal=True` (the union of keys
was always computed internally and thrown away; rows now carry `same`).
Only the `/datasets/compare` caller opts in — the legacy compare/chip-compare
tabs and Trends keep the differences-only default.

`_dataset_compare.html`'s Parameters tab renders: differing rows FIRST with
the existing `cell-diff` highlight (ref column unchanged), then one
"Show N identical parameters" toggle revealing a collapsed tbody where each
identical row shows its single value once ("same in all") — compact,
at-a-glance. Tab badge reads "N diff / M".

## ⑨ Sidebar data search: bigger + run-id fast path

- `.sidebar-filter` font 1.05em → **1.18em**, textarea min-height 1.9rem →
  **2.15rem** (box slightly bigger, padding untouched — the style pins in
  `test_web.py` were amended with the change).
- `_entry_matches`' free-text branch gains a digits-only fast path: `780`
  matches run id #780 exactly or as a prefix (#7801). Run ids were
  deliberately excluded from free text (any digit substring-matched
  everything); a digits-ONLY query is unambiguous, and name/date matches
  still count first. The `id:780` scope is unchanged.

Pins: `TestCompareParamsFullR16` (union + same flags + default-unchanged +
page render: highlight, collapse toggle, badge),
`TestSidebarRunIdSearchR16` (exact/prefix/name-digits/non-digit unchanged).
