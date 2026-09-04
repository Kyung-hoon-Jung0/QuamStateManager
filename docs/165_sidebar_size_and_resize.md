# 165 — rows people can read, a box that says it is tickable, windows with edges

Three customer asks, all about the same thing: controls that do not announce
themselves.

## Rows, badges and names, ×1.25

Multiple users agreeing, independently: the run rows are too small to read.
docs/157 had already raised them 0.95 → 1.06em; this is a further 1.25×.

```
--tree-entry-label-font   1.06em → 1.32em     (measured: 16.6px → 20.6px)
--tree-date-label-font    1em    → 1.32em
```

The run-number badge and the experiment name are each **1em of the row**, so
they follow from the one number — there is no second value to keep in step. The
date header is a *sibling*, not a child, so it takes the value explicitly; its
own comment carries the invariant that a date header is never smaller than the
rows beneath it, and leaving it at 1em would have quietly inverted that.

## The tick box: three states

The box was 18px with a mark that existed **only when ticked** — so an empty box
said nothing about being tickable, which is exactly what the report was.

* **empty** — the tick is there, grey, `opacity: 0.34`. A hint, not a tick.
* **hover** — `opacity: 0.8`. It comes up under the pointer.
* **ticked** — the box fills SM-blue, the tick turns white at full opacity.

The geometry lives on the **base** `::after` rule and only colour and opacity
change per state, so the three cannot drift into three different shapes. The box
grows 18 → 22px: a control that stays 18px beside 1.32em text reads as an
afterthought.

## Every edge resizes, on all three tool windows

> "크기 조절을 할수있으면 좋겠다 — 마우스로 edge에 가져갔을 때"

CSS `resize: both` gives exactly **one** grip, in the bottom-right corner, and
only the Calculator and the Config Manual even had the rule — Settings had
nothing. `FloatPanel.resize` (the docs/141 §4u shared core, which is where the
three windows already share their drag) gives all of them every edge and every
corner: the cursor changes as the pointer crosses a 6px border band, and a drag
from there resizes.

**Grabbing an edge floats the panel first.** Anchored, a panel is pinned by
`right` and `top: 100%`, so a north or west drag would have to move an origin it
does not own and the box would grow the wrong way. Floating it gives it explicit
left/top — which is also what a person means by grabbing a window's edge. The
existing viewport clamp then keeps it on screen.

Minimums are read from **each panel's own computed style**, so every window
keeps the floor its CSS declares instead of a number repeated in the core.

Measured in real Chrome: Calculator 348→408px wide and 560→602 tall, Settings
296→356 and 894→936, Config Manual 560→620 and 720→762 — each floating on grab.

## Pins

`test_sidebar_select_ux.py` — the two docs/161 contracts were **rewritten**, not
loosened: the box pin now states 22px and why, and the tick pin asserts the
three states in order (empty is a hint `0 < opacity < 0.5`, hover is strictly
louder, ticked is white at 1.0). A new `TestRowSize` pins that the badge and the
name follow the row, and that the date header is never smaller. 6/6 mutations.

`float_panel_selfcheck.cjs` — the band reading, the cursor, both edge
directions, the floor and where the moving edge stops against it, and that a
press in the panel BODY is left alone. That last one exists because removing the
band guard is neutralised by a downstream check and looks harmless: what it
really does is `preventDefault` on every press inside the window, which is every
button the window contains. 6/6 mutations, and it took that assertion to make
the sixth fail.
