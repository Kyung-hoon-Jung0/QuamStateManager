# 161 — The sidebar's tick box is a real control, and Compare makes room

**Date:** 2026-09-03 · **Branch:** `feat/calc-window` (sixth commit)

## The asks

> 왼쪽 data 목록 panel에서 사용자가 실험을 선택하는 네모칸 박스가 너무 작고, 무엇보다
> 이게 무슨 기능인지도 모르는 사람들이 많아. … modern하게 … 혹은 뭔가 다른 UI/UX가 있을까?

> compare selected를 누르면 기존에 보고 있던 panel들은 최소 크기로 그냥 줄어들게 만들자.

## 1. The tick box — the judgment

The box was a 12 px native checkbox on rows that docs/157 had just raised to
16.6 px text: proportionally tiny, and styled by Pico's defaults, so it read
as decoration. The user floated 1.5–2× wider / 2× taller.

Decision: **18 px, SQUARE** (1.5× in both axes), not a wide box. A control
wider than tall reads as a text field; the modern selection idiom (Linear,
GitHub, Notion row selection) is a square, rounded box with a soft outline
that fills solid with a white check — recognisable as "a thing you tick" at a
glance, and quiet until it is ticked. Built with `appearance: none`, so the
theme's own blue paints it in both themes; the row hover brightens the
outline (the box answers the cursor before it is touched); focus gets a ring.
Vertical 2× (24 px) was rejected: it would dominate a 32 px row.

What the box is FOR was the bigger half of the report, and a bigger box does
not say it. Two additions:

- the box carries a tooltip — *Tick to select this run — Compare Selected
  diffs 2–5 ticked runs side by side, Trend Tracker plots them (Shift+click
  ticks a range)*;
- a one-line hint under the compare buttons, **only while nothing is
  ticked** — *☐ Tick runs in the list below to compare or trend them (2–5).*
  Once a tick exists the count on the button ("Compare Selected (2)") says it,
  and the hint gets out of the way (`syncCompareCount`).

Alternatives considered and not taken: a hover-only box (hides the very thing
nobody found), a separate "select mode" toggle (a mode to discover on top of
a control to discover), a per-row "Compare" button (three of them per row is
noise on a 2,655-run list).

## 2. Compare makes room

Compare Selected / Trend Tracker land in the TOP pane, while a run detail
opens the inspector at 85 % (the docs/141 customer preset) — so the result
arrived under a 15 % sliver and the report was "did the compare open at
all?". Now the compare form's `htmx:beforeRequest` collapses the inspector to
the user's ⤓ preset (default 15 %) whenever it holds content and is expanded
— the same mechanism as the r13 ⑦ menu-navigation collapse, with a wider
gate (any inspector content, since the result always lands on top). The
sizes are saved first, so the table-pane swap's `initSplit()` keeps them. The
detail content stays; ▲ brings it back.

## 3. Measured in real Chrome (headless CDP, the CQT 2,655-run archive)

| step | observed |
|---|---|
| box | 18×18, `appearance: none`, 5 px radius, tooltip |
| nothing ticked | hint shown |
| one tick | hint hidden; row tinted; box solid blue with a white check (dark and light themes screenshotted) |
| a run detail opened | table 98 px / inspector 572 px |
| second tick → Compare Selected | table 572 px / inspector 98 px, the Diff (A #2560 vs B #2559, figures side by side) on top |

## 4. Pinned

`tests/test_sidebar_select_ux.py` (the box rule, the checked fill + check
mark, the hover rule, the tooltip, the hint markup + toggle, the beforeRequest
collapse hook with its wider gate vs the menu one) and two new asserts in
`sidebar_compare_selfcheck.cjs` (hint shown at 0 ticks, hidden at 1).

## The pre-customer review — two layout fixes (2026-09-04)

- **F-LAYOUT-HINT.** `.compare-hint` is `flex: 1 1 100%`, which only reads as "a
  one-line hint UNDER the buttons" inside a WRAPPING flex container. Its parent
  `.compare-buttons` was `display:flex` with no `flex-wrap`, so the hint stayed on
  the buttons' line and, with basis 100% against the buttons' basis 0, absorbed
  all the free space — collapsing both compare buttons to min-content and breaking
  them over two lines on every page load (the hint shows precisely when nothing is
  ticked). Adding `flex-wrap: wrap` puts the hint on its own line and restores
  full-width one-line buttons. Real Chrome (headless, 1500×950): the two buttons
  now share one line at 127 px each with the hint below, instead of 75 px towers.

- **F-SETTINGS-TALL** (docs/160's Undo group tipped it over). The Settings popover
  is anchored at the viewport top and the drag/reflow clamp never lets it go
  higher, so once its content is taller than the viewport the bottom group is
  unreachable — and there was no scrollbar (`overflow` was the default `visible`).
  `.settings-dropdown` now caps at `calc(100vh - 12px)` with `overflow-y: auto`.
  Real Chrome: `max-height 840px`, `overflow-y auto`, `scrollHeight 870 >
  clientHeight 838` — it scrolls.

Pinned by `tests/test_sidebar_select_ux.py::TestHint::test_the_compare_buttons_wrap_so_the_hint_drops_below`
and `::TestSettingsPopoverScrolls`, both mutation-checked, plus the real-browser
measurement above.
