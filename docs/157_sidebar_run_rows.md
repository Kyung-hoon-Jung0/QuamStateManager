# 157 — The experiment list: larger rows, the run number as a badge

**Date:** 2026-09-03 · **Branch:** `feat/calc-window` (second commit, after docs/156)

## The ask

Customer feedback relayed by the user:

> left panel의 실험 목록. font size 더 키우자. 특히 실험 번호가 눈에 잘 안띈다고 함.
> modern함은 유지하면서 어쨌든 사이즈는 좀 키워보자. 실험명은 두줄로 나와도 좋으니
> 글씨가 좀 더 큰게 좋은 듯함.

Three requirements: bigger text, the run number must stand out, a name may take
two lines. Measured before (root 20 px, sidebar 15.64 px): run number **13.4 px**
mono weight 600, name **14.9 px**, row 23 px, sidebar 260 px.

## 1. What shipped (CSS + one Jinja filter)

- **Rows 0.95 → 1.06em** (`--tree-entry-label-font`; 14.9 → 16.6 px here), the
  date-group header 0.9 → 1em so a header is never smaller than the rows under
  it, row padding 0.06 → 0.18rem.
- **The run number is a badge** (`.run-id`): 1em bold tabular-mono digits on a
  13 % SM-blue field, 4 px radius, inline-block at the start of the row. Same
  colour vocabulary as the date headers, one step heavier; it reads as a
  column down the list because every row starts with it at the same x.
- **One text flow, not a flex row.** `.tree-entry-click` is `display: block`;
  the badge is inline and the name flows after it, wrapping under it. The first
  attempt was a two-column flex row (badge column, name column wrapping under
  itself) — measured in the 260 px sidebar it left **113 px** for the name and
  even `38_two_qubit_xeb` took two lines. Measured now (300 px sidebar): every
  short name one line (32 px), every long name in this archive two lines
  (57 px), including the 37-character `10_qubit_spectroscopy_vs_coupler_flux`.
- **Names wrap at their own word joints.** `soft_breaks` (app.py) renders the
  name with a `<wbr>` after every `_`, each piece HTML-escaped, so a long name
  breaks as `34b_cz_phase_` / `compensation_error_amp` instead of mid-word;
  `<wbr>` carries no text, so `textContent`, the sidebar search and copy see
  the plain name, and the `title` attribute stays plain.
- **Sidebar default 260 → 300 px**, max 300 → 420 px. A width the user
  dragged is persisted (`quam_sidebar_width`) and wins, unchanged; the
  resizer's own clamp (160–640) already reaches past the new max.
- **Compact mode** (`body.exp-list-compact`, the rows toggle) keeps its
  one-line ellipsis: a `<wbr>` is a break opportunity even under
  `white-space: nowrap` — measured 51–73 px compact rows before the fix — so
  compact hides the wbr (`display: none` makes no box and no opportunity).
  Measured after: every compact row one line.

## 2. Measured in real Chrome (headless, CDP, the CQT 2,655-run archive)

| | before | after |
|---|---|---|
| run number | 13.4 px, 600, plain | 16.6 px, 700, badge 59×21 px |
| name | 14.9 px | 16.6 px, breaks after `_` |
| short row / long row | 23 px / 44–65 px (mid-word breaks) | 32 px / 57 px (two lines, word joints) |
| sidebar | 260 px | 300 px |
| light theme | — | badge `rgb(1,114,173)` on a 13 % field, checked |

## 3. Pinned

`tests/test_sidebar_run_rows.py` — the filter (break after every `_`, none →
"", piecewise escaping with `<wbr>` the only markup), the macro using it with a
plain `title`, the row tokens, the badge rule, the one-flow rule, the compact
wbr rule, the sidebar defaults + the resizer clamp. Existing sidebar pins
(`test_web` tree/sidebar classes, `test_lazy_scale`, `test_sidebar_root_row`,
`test_sidebar_compare`, `test_sidebar_nav_pill`, `test_ui_readability`,
`test_search_hint`, `test_misc_ui`) re-run green.

## 4. Not done, on purpose

- No hanging indent for a wrapped name (the second line starts under the badge,
  not under the name's first character): a hanging indent needs the badge's
  width, which varies with the digit count (`#42` vs `#10157`).
- No line clamp: the customer said two lines are fine, and a clamp would hide
  the suffix that often distinguishes two experiments (`_vs_flux` / `_vs_power`).
