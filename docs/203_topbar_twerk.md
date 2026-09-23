# docs/203 — The whole screen shook on a Live State Edit commit

2026-09-23. Customer report, on-site: "live state에서 parameter 업데이트 할때 가끔
SM화면 전체가 마구 흔들린다 (twerk)". The user sees it rarely too.

## 1. What was measured

Real headless Chrome over CDP, the Layout Instability API (`layout-shift`
entries, i.e. what was actually PAINTED), a per-frame sampler of the top bar,
the window scrollbar and `--topbar-height`, and a REAL cell commit on a copy of
the customer's `260907_KRS_5Q` chip (Enter on `x180 amp`, then `POST /undo`).
Baseline = untouched `origin/main` (7fff85a), same instance copy, same chip.

Per commit on the baseline, 768 px tall window:

| width | top bar | content below moves | painted shifts | scrollbar flash | RO loop error |
|---|---|---|---|---|---|
| 1280 / 1366 | 167 → 302 px | 135 px down | 2–3 | yes | yes |
| 1536 | 173 → 265 px | 92 px | 1–2 | yes | yes |
| 1707 | 173 → 258 → 251 px | 85 then back 7, again 1.5 s later | 3 | yes | yes |
| 1920 | 173 → 186 px | 13 px | 1 | yes | yes |

Undo, Apply and an auto-apply flush reverse it, so every edit → apply cycle
moved the whole page down and back up — with Auto-Sync armed, once per commit.

## 2. Mechanism

1. The pending tray lives in the top bar's left group, which is a WRAPPING
   flex row. A commit adds `● N unsaved · Review · Apply to live now`, the
   badge becomes `Working state · N unsaved`, Auto-Sync gains `(N pending)` —
   the group wraps one or two rows taller. Everything below moves by that.
2. The layout was sized `calc(100vh - var(--topbar-height))`, and that
   variable is published by a ResizeObserver AFTER the bar has already grown.
   For that frame the document overflowed by the growth, so a window scrollbar
   appeared, the bar re-wrapped 15 px narrower (a different height), the
   publish landed, the scrollbar went, the bar re-wrapped again. That is the
   multi-shift "shake" and Chrome's `ResizeObserver loop completed with
   undelivered notifications`. It depends on where the wrap points fall, which
   is why it was "가끔".

Ruled out by measurement: sub-pixel rounding in `TopbarHeight` (Chrome needs
≥ 0.5 px of overflow to create a scrollbar; 0.2 px did not), grid
virtualization, scroll restoration, the Plotly observers.

## 3. Fix

- **The shell is a flex column** (`style.css`, `@media screen`,
  `body:has(> .app-layout)`): `body` is `100vh` / `overflow: hidden`,
  everything above the layout keeps its own height, `.app-layout` takes what
  is left (`flex: 1 1 auto; min-height: 0`), and `#sidebar`/`#main` drop the
  `calc(100vh - …)` cap. The layout is sized in the SAME layout pass as the
  bar, so no document overflow — hence no scrollbar feedback — can exist.
  `--topbar-height` is still published (fallback rules keep it) but nothing in
  the shell depends on it. Screen-only: printing still flows.
- **`.shell-head`** (`base.html`) wraps the bar and every banner slot in one
  block box. A flex container never collapses its items' margins, so with the
  slots as direct children each gap between stacked banners doubled (measured:
  layout 10 px shorter with three banners). Inside one block the margins
  collapse as before — measured identical geometry to baseline on 12 pages.
- **`window.TopbarHold`** (`app.js`): the left group may GROW, but never shrinks
  back while the window keeps its width. The tallest height observed is held
  as `--topbar-hold` (a variable, so `html.topbar-hidden`'s `min-height: 0`
  still wins), rows pack to the top (`align-content: flex-start`), so the held
  space is blank space at the bottom of the bar and nothing in it moves. The
  hold is released — and immediately re-taken at the CURRENT height — on a
  width change and on a navigation (`htmx:pushedIntoHistory`, `popstate`).
  The EXACT observed height is held: rounding it up made the min-height itself
  a resize, and the observer looped (measured: one RO error per first commit,
  gone after the change).

## 4. Result (same probe, fix)

| width | 1st commit | undo | 2nd commit | 2nd undo | RO errors | scrollbar flash |
|---|---|---|---|---|---|---|
| 1366 | one 135 px step | page still | page still | page still | 0 | none |
| 1707 | one 85 px step | page still | page still | page still | 0 | none |
| 1920 | one 13 px step | page still | page still | page still | 0 | none |

"Page still" = no `layout-shift` entry involving anything outside the bar
(the remaining tiny entries are the tray's own items re-flowing inside it).

## 5. Trade-off, stated

After the first edit the bar keeps its taller height until the window is
resized or the user navigates. At 1366 × 768 that is ~135 px of blank bar
while the tray is clean. The alternative — letting it shrink — is exactly the
bounce the customer reported. The first edit on a page still steps the layout
down once; removing that too needs the tray's action cluster to stop living in
a wrapping row (a layout redesign, not done here).

## 6. Pins

`tests/test_topbar_twerk.py` (10) + `tests/topbar_hold_selfcheck.cjs`
(14 asserts, executes the shipped module). Mutation sweep: 11/11 red (shell
overflow, align-content, #main cap, `@media all`, shrink allowed, release
without re-hold, height-only resize releasing, hidden guard, `Math.ceil`,
navigation release, inline min-height). The `.shell-head` pin was added after
the sweep and was mutation-checked on its own (the diagnostics slot moved out of
the wrapper: red).

The full-suite run caught one pin of MINE breaking a neighbour:
`test_agent_pill.py::test_the_pill_and_the_bar_give_way` reads a fixed
2,200-character window after `.agent-pill`, and the hold rule's comment,
inserted mid-block, pushed that window's last rule out. The rule now sits
after the pinned block; nothing about the pill changed.

A harness trap re-paid: the module first guarded on `window.ResizeObserver`
and constructed a bare `ResizeObserver` — the docs/125 guard/call mismatch;
the selfcheck caught it (bare global absent in the Node realm), and the call
now goes through `window.`.
