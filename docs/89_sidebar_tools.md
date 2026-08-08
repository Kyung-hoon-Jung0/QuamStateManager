# 89 — Settings and Calculator, where people look

*Status: shipped 2026-08-09. Branch `feat/sidebar-tools`.*

## The report

> 현재 "계산기"와 "setting" 버튼이 오른쪽 상단에 있어서 너무 불편하고
> 가시화되지 않는다는 피드백이 있어. … Projects 위에, 즉, 왼쪽 메뉴 최상단에
> Settings (톱니로고) 그리고 Calculator (계산기로고) 로 두개 넣어달라고 하네?
> … 그리고 로고도 좀 더 잘 보이고 제대로 상징성이 있는 것으로 바꿔달라는데?

Both parts were fair. The top-right of a wide window is the corner furthest
from where the eye lives (a left sidebar), and the calculator's glyph was
`&#8757;` — **U+2235 ∵, the "because" sign**. It has nothing to do with a
calculator. Not recognising it was the correct response.

## What moved

The two tools are now the first thing in the sidebar, as a labelled row above a
divider so they read as *tools* rather than as two more destinations. Icon +
label, because the complaint was visibility and the sidebar has the width for
words. Settings first, per the user.

## Two structural traps

Moving the markup as-is would have broken twice over, and both are now pinned:

**The sidebar collapses to nothing.** `.sidebar-collapsed #sidebar` is
`width: 0; opacity: 0; pointer-events: none` — not an icon rail. Anything
inside becomes unreachable, and collapsing is a real, persisted feature
(`quam_sidebar_collapsed`). So a compact icon-only pair sits in the topbar and
appears **only** while the sidebar is collapsed. Exactly one of the two rows is
ever visible, so this is a fallback, not a duplicate control — the same
reasoning as `.topbar-reveal`, which exists because "Hide top bar" lives inside
the top bar. It also fixes an existing awkwardness: with the top bar hidden,
Settings used to be unreachable entirely.

**The sidebar scrolls.** `#sidebar` is `overflow-y: auto`, which **clips** an
absolutely-positioned child. Both popovers were
`position: absolute; right: 0; top: 100%` inside their topbar `<li>`. They now
live at **body level** and are placed by `_anchorPopover(pop, btn)` in viewport
coordinates from the trigger's own rect — below-and-left-aligned, flipping above
or clamping to the right edge when the panel would leave the window (the
calculator is ~348×560 and does not fit under a trigger near the bottom of a
short window). `.pop-anchored` has to reset `right`/`bottom` explicitly, since
the base rules still carry `right: 0; top: 100%` from the topbar era.

Which trigger is used is decided from the collapsed **class**, not from layout
(`offsetParent`/rects): that is the actual condition, it needs no layout pass,
and it stays checkable under jsdom. A trigger the user really clicked always
wins. A popover the user has **dragged** (`.calc-floating`) keeps where they put
it rather than snapping back.

## The icons

Inline SVG, 24×24 viewBox, 1.7 stroke on `currentColor`, so they inherit
light/dark/colorblind themes and the UI scale for free and stay legible at
16–18px. Emoji (🧮 / 🔢) would render differently on every platform and cannot
take the theme colour.

* **Settings** — an 8-tooth gear. Six teeth read as a flower at this size.
* **Calculator** — body + display bar + a 2×3 keypad. The keypad is what makes
  it unmistakably a calculator rather than a phone or a document.

## Also

The calculator had **no keyboard shortcut at all** (Escape only closed it),
which is part of why it went unused. **Alt+C** toggles it — clear of Ctrl+K (the
palette) and of every browser Ctrl+&lt;letter&gt; — and is ignored while the
focus is in an input/textarea/select/contenteditable, so it can never eat an
Alt-combination someone is typing. It is advertised in the button's tooltip.

## Pins

`tests/test_sidebar_tools.py` (18) — the row's position/labels/order, the old
wrappers being GONE (moved, not copied), SVG icons with no `&#8757;`, the
fallback and the CSS gate that hides it, **the premise of the fallback** (that
`.sidebar-collapsed #sidebar` really is width 0 — if that ever becomes an icon
rail the fallback may go, but not before), the popovers rendering outside
`</aside>`, the anchored-rule override, and that the sidebar still scrolls.
`tests/sidebar_tools_selfcheck.cjs` (22) — one popover per tool against two
triggers, fixed anchoring from the trigger rect, the collapsed case picking the
topbar trigger and clamping on-screen, the singleton, aria on both triggers,
Alt+C including the typing guard, and the dragged-popover carve-out.
`tests/test_tab_focus.py` — unchanged and green: the calculator's Tab-between-
fields contract survived the move.
