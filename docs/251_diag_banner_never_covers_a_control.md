# docs/251: the late diagnostics banner never covers a control

2026-10-03, agent validation campaign, finding **C-13** (also filed as **I-03**,
`D:\work\sm_qa_rigs\agent\FINDINGS.md`).

## Report

The crash banner ("1 error on chip would crash a node run ... Review diagnostics
✕") floated as a fixed box at the bottom-left of the window. It covered real
controls: the Agent composer's select, the plan-mode select at 1366 px, and Agent
Setup's Test buttons. I-03 found the same for Setup's "Show the questions" at
1366x900, where elementFromPoint returned the banner. The only way to reach those
controls was to dismiss the banner. Screenshot:
`D:\work\sm_qa_rigs\agent\rC\shots\4-10_approval_cards_1366.png`.

## Measured on origin/main (4bde3837)

Setup: real headless Chrome over CDP. SM ran from a `git archive` of origin/main
against a copy of the rC rig chip (one crash-class error,
`qubits.qB3.xy.RF_frequency`). Each case opened a fresh tab, so nothing was
reserved. The pointer was then moved to the place the user works. A control
counts as **covered** when elementFromPoint at its centre returns the banner. The
centre is taken inside the control's own clip box, so a control that is merely
scrolled out of view does not count.

The banner box was `[20, 629, 760 x 119]` at 1366x768 and `[21, 938, 760 x 121]`
at 1920x1080. It stayed an overlay in every case below: working inside a page is
never one of the old docking moments.

| page, size, theme, sidebar | covered while working |
|---|---|
| Agent, 1366, dark, open | composer select "which CLI drives" (373,739), name input, "1Q bringup", "readout tuneup", sidebar Pairs/Resonators/Flux: 7 |
| Agent, 1366, light, collapsed | composer textarea (683,695), composer select (67,739), name input, 3 preset chips: 6 |
| Agent, 1920, light, open | composer select, name input, 2 chips, sidebar Trends/2Q/1Q/Read Fid.: 8 |
| Agent Setup, 1366, dark, open | **Test claude** (391,739), **Test codex** (490,739), 3 sidebar links |
| Agent Setup, 1920, light, collapsed | "6. Test", **Test claude**, **Test codex** |
| Live State Edit, 1366, light, open | qA5/qA6 `f_01` cells, two row-pin buttons, two toggles, 3 sidebar links: 9 |
| Pulses, 1366, light, collapsed | table rows (clickable `td`s, not in the control selector) |
| Datasets, 1366, dark, open | 3 sidebar links |
| floating agent panel on Pulses, 1366, dark, collapsed | the panel's composer select (730,718) |

A second hazard showed up in the same runs. An edit that creates the first error
makes the banner arrive while the pointer rests on the page. When that happened,
the overlay landed **on the element under the pointer**: the Agent composer, a
Setup Test button, the floating panel's select. A click aimed there would hit the
banner instead. Before: `ev_docs251\before\base3_results.json`, `A_after.same =
false`.

## Why it stayed

The overlay was meant to be a short wait. It docked into the flow only at three
moments:
- the main pane was replaced;
- the pointer was over the head block;
- the tab was hidden.

A user who works inside one page never produces any of these: chatting with the
agent, testing a CLI in Setup, editing a grid. The banner only floats at all on
the first banner a tab sees (a reload reserves its space), or when an edit
creates the first error. Still, in those cases it floated for as long as the
user stayed on the page.

## Decision, against the no-shift rule

The binding rule: never move content under the pointer. A late banner pushed a
Pulses click two rows down once (verifier P3, w7/adaptive).

Rejected:
- **A compact pill at a fixed spot.** No spot is free of controls on every page.
  With the sidebar collapsed, the bottom-left corner is the Agent composer's
  select (x 20-112, y 725-755 at 1366x768). The bottom-right holds the agent
  panel, Send and the toasts. The top holds page headings and toolbars.
- **A reserved bottom inset.** A bottom-anchored bar such as the Agent composer
  moves up by the inset. If the pointer is on the composer at that moment, that
  is the shift the rule forbids.

Chosen: only the flow covers nothing. So the banner joins the flow as early as a
measurement proves that nothing under the pointer moves.

1. **It floats over exactly the box it will take in the flow.**
   - Before: `position: fixed`, bottom-left.
   - Now: `position: absolute; top/left/right: 0` inside its own zero-height
     slot. The slot is `display: flow-root`, so the banner keeps the same
     margins and width in both modes, and therefore the same wrap and height.
   - Measured: the floating and the docked box are identical, `[20, 57.78,
     1326 x 74.42]` at 1366 and `[21, 64.66, 1878 x 76.17]` at 1920.
   - So joining the flow never moves the banner itself, and a pointer resting on
     the banner is a safe moment to dock.
2. **It docks when the dock is measured to be safe** (`_diagJoinFlow`).
   - Trigger: every pointer move, press, key or scroll while it floats. Trials
     are throttled to one per frame and at most one every 120 ms.
   - The trial joins the flow and re-measures the element under the pointer.
   - If that element moved by `dy`, the scroller it sits in scrolls by exactly
     `dy`. This happens only when the scroller moved by the same `dy`, has the
     room, and still contains the pointer. It is the same idea as CSS scroll
     anchoring: the content under the pointer stays put.
   - If the element under the pointer is the same element in the same place, the
     dock is kept. Otherwise everything is put back in the same task, before
     any frame is painted.
   - The commit test is the rule itself, measured, not a list of moments that are
     assumed safe. Places that pass: the banner itself, the head block, a fixed
     panel, the bottom-anchored Agent composer, and a row in any scroller with
     room.
3. **It never appears under a resting pointer.**
   - If the pointer is inside the banner's box when it arrives and the dock
     cannot be kept, the banner is held (`data-held`, `visibility: hidden`, not
     hit-testable).
   - It is shown as soon as the pointer is elsewhere.
4. **If the pointer position is unknown, it floats.**
   - This happens when no pointer event has fired since the page loaded.
   - Nothing can be proven safe, so it floats instead of shifting the page.
   - A key press does not force it in.
5. **The unconditional moments stay:** main pane replaced, tab hidden, pointer
   left the window (`pointerout` with no `relatedTarget`; an iframe does not
   count).
   - They also keep the pointer's own scroller still. Before, a sidebar click
     that swapped the main pane slid the sidebar under the pointer.
   - The pane that was just replaced is never scrolled, because that would hide
     the top of a fresh page.

What the user sees: the banner appears at the top, where it will stay. The first
pointer move over the page usually makes it part of the flow with nothing visible
moving. The scroller under the pointer keeps its content in place. Its top ~94 px
moves above the fold and is one scroll up.

## Changes

- `web/static/style.css`
  - `#diagnostics-banner-slot { display: flow-root; position: relative; }`
  - The floating rule is now `absolute; top/left/right 0` with the in-flow
    margins. It used to be `fixed; left/bottom 1rem; max-width 760px`.
  - New held rule.
- `web/static/app.js`, the docking block:
  - new: `_diagJoinFlow`, `_diagScrollChain`, `_diagHoldCheck`,
    `_diagBannerTry`, `_diagCommitFlow`;
  - pointer tracking in capture/passive mode (`pointermove`, `pointerover`,
    `pointerdown`), plus `pointerout`, `keydown`, `wheel` and `scroll` triggers;
  - the echo `scroll` from a reverted trial is ignored;
  - `_diagBannerDock(skip)` now goes through `_diagJoinFlow(force)`;
  - the remembered height is stored in both modes, since the widths now match.
- `base.html`, `_diagnostics_banner.html` and `routes.py` are untouched. So are
  `mcp.py` and `agent_api.py`.

## Pins

- `tests/diag_banner_dock_selfcheck.cjs`, driven by
  `tests/test_diag_banner_dock.py`: **31 assertions**.
  - It runs the shipped app.js under jsdom with a modelled layout: a head block,
    the banner, a top strip outside any scroller, a scroller with room, a
    fixed-height scroller at its end, and a bottom-anchored composer.
  - It also checks the style.css contract the model relies on.
- `tests/pulses_urlsync_selfcheck.cjs`: the one pin on the old geometry changed
  from "out of the flow (fixed)" to "out of the flow (absolute, over its own
  in-flow box)". Its other 17 assertions are unchanged and pass.
- **Mutation sweep: 14 / 14 red.** It ran on a copy of the code
  (`ev_docs251\mutate.py`). The mutations:
  - float back at the bottom-left (fixed);
  - no flow-root;
  - held banner visible;
  - no scroll compensation;
  - dock without measuring;
  - never held;
  - held forever;
  - pointer leaving the window ignored;
  - pane swap scrolls the fresh pane;
  - scroll even when the pointer ends above the scroller;
  - no room check;
  - dismissed float left behind;
  - unknown pointer docks;
  - pointer moves do not trigger a trial.
- Related suites, env cqt, 65 passed:
  - `test_diag_banner_dock`, `test_pulses_url_banner`, `test_topbar_twerk`;
  - `test_diagnostics_banner_routes`, `test_diag_deep_validate`,
    `test_trends_render_queue`;
  - `test_diagnostics_freshness`, `test_lint_single_flight`,
    `test_preflight_diagnostics`.
  - `test_web.py` (it mentions the banner): 558 passed, 19 skipped.

## Browser matrix (fix)

Real headless Chrome, port 5107, with a copy of the rC chip, calibrations, data
and agent plans/approvals. Each case is a fresh tab, so the banner always
arrives unreserved.

- **B**: the page loads with no pointer known. Then the pointer moves to the work
  point.
- **A**: the banner re-arrives (as after an edit) while the pointer rests on the
  work point.
- **H**: the banner arrives while the pointer rests on a control inside its box.
- **J**: away (Datasets) → back → reload.

Δ is how far the element under the pointer moved.

| case | B: after the first move | Δ | A: re-arrival, pointer resting | covered once working |
|---|---|---|---|---|
| Agent 1366 dark open (+H, J) | docked | 0 | docked at arrival, Δ 0 | 0 |
| Agent 1366 light collapsed (pointer on the composer select) | docked | 0 | docked, Δ 0 | 0 |
| Agent 1366 dark collapsed, topbar hidden | docked | 0 | docked, Δ 0 | 0 |
| Agent 1920 light open (J) | docked | 0 | docked, Δ 0 | 0 |
| Setup 1366 dark (Test buttons at the bottom, J) | docked | 0.43 | floats at top (end of scroll range: no room), Δ 0 | 0 |
| Setup 1920 light collapsed | docked | 0.17 | floats at top, Δ 0 | 0 |
| Live Edit 1366 light open (J) | docked | 0.42 | docked, Δ 0.42 | 0 |
| Live Edit 1920 dark collapsed | docked | 0.17 | docked, Δ 0.17 | 0 |
| Pulses 1366 light collapsed (+H, J) | docked | 0.42 | docked, Δ 0.42 | 0 |
| Pulses 1920 dark open | docked | 0.17 | docked, Δ 0.17 | 0 |
| Datasets 1366 dark (4 runs, short page, J) | floats on a row; docks on the sidebar link, Δ 0.42 | 0 | floats, Δ 0 | 0 |
| Datasets 1920 light collapsed | floats on a row; docks on the topbar | 0 | floats, Δ 0 | 0 |
| agent panel on Pulses 1366 dark collapsed | docked | 0 | docked, Δ 0 | 0 |
| agent panel on Live Edit 1920 light | docked | 0 | docked, Δ 0 | 0 |

- **C-13 controls by name** (`ev_docs251\after\pm1366_*`, `pm1920_*`).
  - While the banner floated, these were each topmost at their centre at 1366
    and at 1920: the composer select, the composer textarea, and a plan-mode
    select scrolled to the bottom-left of the feed (where the old box sat).
  - After the dock at 1920, the plan-mode select was still topmost.
  - After the dock at 1366, it had moved 94 px down with the feed and sat past
    the feed's bottom edge. The hit there was the composer row, not the banner.
    The pointer was on the composer select, so the feed was not scrolled to
    compensate. The select is one scroll down.
  - The Setup Test buttons were never under the banner in any Setup case.
  - The floating panel's select was never under the banner in either panel case.
- **H.**
  - Agent: pointer on "Connect →". Pulses: pointer on the search box.
  - In both, the banner was held (invisible) and the control stayed topmost.
  - When the pointer moved 140 px down, the banner was shown. On Agent it kept
    floating, because the pointer was on the status strip. On Pulses it docked,
    because the pointer was on the chip map inside the scroller.
- **J.** Away, back and reload all put the banner in the flow (reserved), 0
  controls covered.
- **Console errors: 0** in every case, before and after.
- **Cost:** one failed trial (forced layout and revert) took 1.8 to 3.1 ms.
- **No-shift:** in every A case, the element under the resting pointer had the
  same identity and the same top (within 0.43 px) before and after the banner
  arrived.

Screenshots:
- after the fix: `D:\work\sm_qa_rigs\agent\ev_docs251\after\fix2_<case>_{1_pre, 2_arrived, 3_working, 4_rearrive, 5_held, 6_heldoff, 7_reloaded}.png`;
- before: `...\ev_docs251\before\base3_<case>_*.png`;
- raw numbers: `fix2_results.json` and `base3_results.json`.

Screenshots read directly during the review:
- after:
  - Agent 1366 dark, `2_arrived`, `3_working`, `5_held`, `6_heldoff`;
  - Agent 1366 light collapsed, `2_arrived`;
  - Agent with the top bar hidden, `3_working`;
  - Setup 1366, `3_working`;
  - Live Edit 1366, `3_working`;
  - agent panel on Pulses, `3_working`;
  - Datasets 1920, `3_working`;
  - Pulses 1366, floating vs docked, `after\fix1_pulses_1366_light_col_{2_arrived,3_working}.png`.
    These are from the first matrix round, before the held state was added. The
    floating and docked geometry is the same;
- before:
  - Agent 1366 light collapsed, `3_working`;
  - Setup 1366, `3_working`;
  - agent panel on Pulses, `3_working`.

## Open items

- **No known pointer** (keyboard-only use since the load, or a headless driver).
  - The banner floats at the top over the top strip until the first pointer
    move. That strip holds the page heading, the toolbar and the sidebar's top
    links.
  - Measured in that phase: 2 to 8 top controls covered, for example Pulses'
    search and "+ New pulse", the Agent wire-strip links, and the Datasets
    toolbar at 1920.
  - The first move to anything that passes the measurement docks it, and that
    includes moving onto the banner.
  - Headful Chrome may report a pointer position after load through synthetic
    boundary events. This could not be tested headless: after `Page.navigate`,
    no pointer event arrived.
- **A pointer resting on content a dock would move, with no scroller to absorb
  it** keeps the banner floating at the top. Examples: a short page such as
  Datasets with 4 runs, the Agent wire and status strips, the end of Setup's
  scroll range. The bottom-anchored controls stay free in this state, and
  pointing at the banner docks it.
- **Sub-pixel residual.** A compensated dock leaves 0.17 to 0.43 px, because
  `scrollTop` is whole pixels and the banner height is not.
- **After a compensated dock**, the top ~94 px of the pointer's scroller (for
  example Pulses' heading and "+ New pulse") sits above the fold, one scroll up.
  This is scroll anchoring's usual trade.
- **Pre-existing, not changed.** With the top bar hidden, the fixed tray pill and
  the floating ☰ overlap the docked banner's first words. The flow geometry is
  the same as on origin/main. This is chrome over the banner, not the banner over
  a control.
