# 156 — The Calculator as its own browser window

**Date:** 2026-09-03 · **Branch:** `feat/calc-window` (cut from `main` at v0.9.9)

## The ask

User feedback, verbatim:

> calculator 창은, 완전히 독립적으로 floating하게 해서, 그냥 다른 브라우저 창으로
> 띄우듯이 못하나요? (현재는 floating이 되지만 SM의 창 내부에서만 floating함)

The Calculator (docs/89, docs/100, docs/141 §4u/§4aj) is a body-level popover:
anchored under its sidebar button, draggable by its header, resizable, remembered.
But it is an element of the page, so `FloatPanel.drag` clamps it to
`window.innerWidth/innerHeight` — it floats only *inside* the SM window. A
researcher wants it beside a Jupyter window or the QUA IDE on another monitor.

Scope decided with the user: **browser mode only** (`qsm serve` / the dev server in
a normal browser). The desktop shell is gated, not served — see §4.

## 1. What shipped

- **`GET /calc-window`** (`routes.calc_window`) — the calculator as a standalone
  document (`calc_window.html`): no base.html, no topbar/sidebar, no htmx, no
  app.js, no bundles. The page ships exactly one script, `calc.js`, plus the
  stylesheet and the theme boot. The route deliberately never calls `_ctx()` —
  the calculator is chip-independent, and rendering it beside a chip must be
  inert (no live-drift self-heal, no chip activation).
- **`_calc_body.html`** — the five sections + the expression footer, now ONE
  partial rendered by both surfaces. The fields are found by id in calc.js, so
  the ids are the contract; the wrapper and the header stay with each surface
  (only the popover has a header). A test asserts every id calc.js names exists
  in the partial and that neither surface carries a field of its own.
- **`_theme_boot.html`** — the theme / font-size / UI-scale boot moved out of
  base.html into a partial both documents include, so the window looks like the
  page that opened it. `openCalcWindow` also appends `?theme=<current>` — a page
  forced light by `?theme=light` (never persisted) opens a light window.
- **The ↗ in the popover header** (`.calc-popout`, left of ×, inside
  `.calc-header-tools` so the drag core ignores it) → `openCalcWindow(this)`:
  `window.open(url, 'quam-calc', 'popup=yes,width=…,height=…,resizable=yes,…')`.
  A *sized* open is what makes Chrome/Edge/Firefox give a WINDOW rather than a
  tab. One window NAME, so a second open can never spawn a second window.
- **One calculator at a time.** The page keeps one reference; while that window
  is alive, ↗, the Calculator button and Alt+C *focus* it instead of opening a
  second calculator in-page (two calculators with two sets of numbers is the
  confusing outcome). The in-page popover closes the moment the calculator moves
  out, and comes back on the next press once the window is closed.
- **The standalone document** (`#calc-popover.calc-standalone`, keyed by both
  calc.js and the stylesheet): computed on load, first field focused, no
  anchoring / drag / outside-click closer (the OS window is the frame), Escape
  closes the *window*, and the window remembers its size and screen position
  (`localStorage["quam_calc_win"]` = `{w, h, x, y}`, written on resize and
  pagehide) so the next open lands where the user left it. The copy buttons
  flash themselves (`.calc-copied`) because there is no app.js toast there.
- **A blocked popup** (`window.open` → null) leaves the in-page popover open and
  never throws.

## 2. Why not simply let the popover leave the window

A DOM element cannot. `position: fixed` is viewport-bound; the only things that
escape the window are `window.open` (a browser window) and, under the desktop
shell, a second native window from Python. The popover stays exactly as it was
for anyone who prefers it — the ↗ is an addition, not a replacement.

## 3. Measured in real Chrome (headless, over the DevTools protocol)

The Claude-in-Chrome extension cannot reach this machine's localhost (a known
limitation recorded in docs/141), so the check drove headless Chrome
(`--headless=new --remote-debugging-port --remote-allow-origins=*`) with
real `Input.dispatchMouseEvent` clicks — a synthetic `.click()` has no user
activation and the popup blocker would have hidden the very thing under test.

| step | observed |
|---|---|
| sidebar Calculator → popover | open; ↗ rendered 25×23 px, `title="Open in a separate window"` |
| click ↗ | page targets 1 → 2, the new one at `/calc-window?theme=dark`; popover `calc-hidden` |
| from the opener, `openCalcWindow()` | returns the live window: `closed=false`, title `Calculator — QUAM State Manager`, `calc-s1-k = 0.0562341` (computed on load), active element `calc-s1-dp`, `#calc-popover` position `static`, border `0px`, no `.calc-header`, theme `dark` |
| Calculator button again | popover stays hidden (the window is focused instead) |
| inside the window | scripts = `[calc.js]`, 5 sections, 8 copy buttons, `0.5*10^(-25/20) = 0.0281171`; typing −6 → factor `0.501187` |
| Escape in the window | page targets 2 → 1 (the window closed), `quam_calc_win = {"w":400,"h":680,"x":0,"y":0}` |
| Calculator button after that | the in-page popover opens again |

## 4. The desktop shell, and why it is gated rather than served

Under pywebview the WebView2 backend handles `NewWindowRequested` itself
(`edgechromium.py` `on_new_window_request`: `Handled=True`, then either the
system browser when `OPEN_EXTERNAL_LINKS_IN_BROWSER` is set, or `load_url` on
the SAME window). A `window.open` there would replace the whole app with a
calculator and offer no way back. So calc.js checks `window.pywebview` at click
time (the shell injects it after navigation completes, so a load-time check is
not reliable) and does nothing, and hides the ↗ on `pywebviewready`. The
in-page floating popover remains the desktop answer.

A true second native window is feasible — a `js_api` object passed to
`webview.create_window` exposing `open_calc_window()` that calls
`webview.create_window(url=f"http://127.0.0.1:{port}/calc-window", …)` at
runtime — but the app has no JS↔Python bridge today (`_on_loaded` is a stub),
and the user scoped this round to browser mode. `/calc-window` is already the
page such a window would load.

## 5. Residuals, stated

- **The reference lives in the page.** SM navigates by htmx swaps, so the
  reference survives ordinary navigation; a FULL reload (chip switch via
  HX-Redirect, F5) loses it. Then the Calculator button opens the in-page
  popover again while the separate window still exists, and pressing ↗
  re-navigates that named window (its typed values reset). Probing for a live
  named window without a reference is not possible without side effects
  (`window.open('', name)` creates a blank window when none exists), and a
  localStorage heartbeat could not focus the window from the opener without a
  user activation in the window's own context. Recorded, not solved.
- Popups may be blocked by an extension or a strict site setting; the popover
  stays, and nothing says why. A one-line hint would need a toast on that path.
- `window.open` position features are advisory; some window managers ignore
  `left/top`.

## 6. Pinned

- `tests/test_calc_window.py` — the route (standalone, every field, no `_ctx`,
  with/without a chip), the one-partial contract, the shared theme boot, the
  light document (exactly one script), the ↗ door and its desktop gate, the CSS
  frame; plus the node driver.
- `tests/calc_window_selfcheck.cjs` — 35 executed asserts over the REAL calc.js
  in two jsdom worlds (standalone / in-page), **8/8 mutations caught**: dropping
  focus-instead-of-open, close-popover-on-popout, the standalone wiring, the
  pywebview gate, Escape-closes-window, pagehide remember, remembered position,
  and the popup-blocked guard each turn the run red.
- Harness lesson repeated (docs/149's shape): calc.js wires on
  `DOMContentLoaded`, which jsdom fires asynchronously — the harness dispatches
  it after evaluating the script, in the browser's own order, before asserting.
  The first draft asserted synchronously and read an unwired standalone world
  as "computed on load: FAIL".
- Existing pins untouched and green: `test_calc.py`, `test_sidebar_tools.py`,
  `test_float_panel.py`, `test_search_hint.py` (the expression placeholder is
  the one allowed hand-written placeholder, now in the partial),
  `test_bundles.py`, `test_unseen_edits.py`, `test_config_manual.py`,
  `test_tab_focus.py`, `test_misc_ui.py`, and all of `test_web.py`.

## 7. The pre-customer review — three window fixes (2026-09-04)

Three defects in the separate-window path, each reproduced against the real
calc.js and fixed with a mutation-checked jsdom pin (`worldD` / `worldE`).

- **F-CALC-DEAD (a regression from the docs/160 §5d guard).** `toggleCalc`'s
  guard passed on `_extAlive` alone and then did `if (_calcWin) { focus(); return; }`
  WITHOUT re-checking `_calcWin.closed`. `focus()` on a closed window is a silent
  no-op, so after the window crashed / was discarded while `_extAlive` was still
  latched true (a second tab's `calc-here`), the sidebar Calculator button and
  Alt+C did nothing — the healing `_focusExternal` path sat behind that `return`.
  Now `if (_calcWin && !_calcWin.closed)`: a closed handle falls through to
  `_focusExternal`, which pings, and — when nothing answers — heals `_extAlive`
  and opens the popover in-page.

- **F-CALC-DUP.** The standalone window only ever SPOKE in reply (it answered
  `calc-ping` / `calc-probe`, said `calc-bye` at pagehide) and never announced
  itself on open, while a page only ASKS once, at its own load. So any SM tab
  already open when the window opened kept `_extAlive = false` and opened a
  SECOND in-page calculator on the Calculator button / Alt+C. `wireStandalone`
  now posts one `calc-here` when it wires up, so every already-open tab latches
  the window immediately. (This also tightens the §5 "full reload loses the
  reference" residual: a reloaded page now learns of the window from its next
  announcement, not only from its own load-time probe.)

- **F-CALC-GROW.** `remember()` stored the realised `innerWidth`/`innerHeight`,
  and `winFeatures()` fed those straight back as the next `window.open` size —
  but the realised inner box is a few px larger than the requested content size,
  so the window ratcheted bigger every open/close cycle (measured +6/+16 px per
  cycle in real Chrome until it saturated the screen). `winFeatures()` now
  records the CONTENT size it asked for (`quam_calc_req`), the window measures
  this browser's frame overhead once at load (`inner − requested`), and
  `remember()` stores `inner − overhead` clamped to the screen — so a pure
  open/close stores the same size and only a genuine user resize moves it.

Pinned by `tests/calc_window_selfcheck.cjs` World D (F-CALC-DUP announce-on-open,
F-CALC-GROW no-ratchet + resize-still-flows) and World E (F-CALC-DEAD closed
handle heals), all mutation-checked.
