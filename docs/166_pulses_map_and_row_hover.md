# docs/166 — The Pulses page becomes navigable, a row hover that was never visible, and the calculator's front door

Five customer items, 2026-09-04. Each was reproduced on the real 20-qubit chip
in real Chrome before anything was changed, and two of them turned out to be
different bugs from the ones reported.

---

## 1. "마우스를 올려놓으면 아무런 음영표시가 안나와서" — the row hover

**Reported**: hovering a pulse row shows nothing, so with 535 rows it is hard to
tell which qubit or pair a row belongs to. The customer named the surface that
works: the data sidebar's folder list.

**What was actually happening.** The hover *did* reach the row. Measured with a
real mouse move over `#pulses-table tbody tr.clickable-row`:

```
BEFORE {"tr":"rgba(0, 0, 0, 0)","td":"rgb(19, 23, 31)","hovered":false}
AFTER  {"tr":"rgb(32, 38, 50)","td":"rgb(19, 23, 31)","hovered":true}
```

The `<tr>` background changed exactly as `.clickable-row:hover` says. Every
`<td>` kept painting `rgb(19, 23, 31)` on top of it, because Pico declares

```css
td,th{background-color:var(--pico-background-color)}
```

and **a cell's background always paints above its row's**. The rule has been
dead on all ten `.clickable-row` surfaces in the app for as long as Pico has
been bundled.

The same covering hid row SELECTION, which nobody had reported:

```
selectedTr  rgb(1, 114, 173)      <- the highlight
selectedTd  rgb(19, 23, 31)       <- what you actually see
```

**Fix.** The paint moves from the row to the cells — the things Pico gave a
background to. The tint is the sidebar's own hover expression (the surface the
customer named), mixed against the cell's own ground rather than `transparent`,
so an opaque cell stays opaque and nothing behind the table can show through it.
The datasets table's private copy of the same intent — also on the `<tr>`, also
dead — is deleted rather than fixed twice.

Pinned by `tests/test_row_hover.py` (5 asserts, 4/4 mutations red). One of the
pins is on **Pico**, not on our own rule: a source-only pin would stay green if
Pico ever dropped the cell background, and then the cell-level rule would be the
unexplained one.

---

## 2. "length나 amplitude를 수정했는데 pulse list에 반영이 안된다"

**Reported by a second customer**; it did not reproduce for the user, and it did
not reproduce for me either — on the first pulse I tried:

```
EDIT   qubits.q1.xy.operations.saturation.length : 20000 -> 20008
AFTER+800 {"rowCells":["","20008","0.01427"]}
```

It reproduces on an **alias**. Editing `x180_DragCosine.length` 44 → 48:

| row | after the edit | the chip holds |
|---|---|---|
| `x180_DragCosine` | 48 | 48 |
| `x180` (alias, the one opened) | 48 | 48 |
| `x90` (alias) | **44** | 48 |
| `y180` (alias) | **44** | 48 |

**Root cause.** `_pulse_rows_touched` expanded `used_by` by ONE hop. A real
chip's chain is two:

```
x90 (alias op)  ->  x90_DragCosine  ->  x180_DragCosine.length
```

The first hop reaches `x90_DragCosine`, whose row is repainted correctly.
Nothing then asked who points at *that*, so the alias sitting on top of it never
learned. `x180` looked fixed only because it happens to be one hop from the
edited pulse.

**Fix.** Walk the reference graph transitively (a worklist; a newly reached root
is enqueued once, so a pointer cycle terminates). The existing 24-root cap still
degrades to the structural whole-table re-fetch — a row showing a stale number
is worse than a re-fetch.

Verified on the chip: after the fix `x90` and `y180` follow to 48 while q2 and
q5 correctly stay at 40. Pinned by
`test_the_expansion_follows_a_pointer_chain_all_the_way`; **the fixture had to
grow the second hop first** (it only had `x180 -> x180_DragCosine`, so the
one-hop expansion could not fail there — the docs/141 §4af lesson again).
Mutation-checked.

---

## 3. The Pulses page gets the chip as a control

**Reported**: 535 rows is too many to navigate; draw the component-page diagram,
slightly smaller, showing qubits *and* pairs, and let a click show only that
entity's pulses. Follow-up during the work: *compact, modern like the rest of
SM, and easy to click with a mouse.*

**One drawing, not two.** The page includes the same `_component_map.html` the
component pages use, so there is no second chip layout to keep in step. Three
knobs were added to that partial, each defaulting to the pre-166 value, so every
other caller's markup is unchanged: `cmap_cell`, `cmap_pick`, `cmap_open_key`.

**The pick lives in one hidden input.** `app.js`'s `htmx:configRequest` rewriter
already rebuilds every pulses-table request path from live DOM state (search
keyword + active channel); `owner` joins it there, so the channel tabs, the
search box, the pagination links and the mutation refresh all inherit the pick
with nothing else to keep in sync. `_pulse_rows_filter` gained the parameter, so
the page and `/pulse/row` still share ONE truth (docs/141 §4l-review) — a
repainted row belonging to another entity answers 204 and leaves.

The pick is an **exact** entity id, never a substring: picking `q1` can never
drag in `q10`. The search box stays the fuzzy one, and the two compose.

**Four things the drawing needed before it was a control**, each measured:

1. **Pair edges were unhittable and invisible.** A `<line>` 2px wide is not a
   mouse target, and its computed stroke on this page was
   `rgb(32,38,50)` at `opacity: 0.22` on a `rgb(19,23,31)` ground — the ground.
   `topo-graph.js` now emits a transparent 14px `cm-edge-hit` line under the
   visible one, `pointer-events: none` by default so no other map's behaviour
   changes, and only the pick mount turns it on.
2. **The `qubits` highlight was wrong here.** A component page's highlight DIMS
   everything that is not its subject — which is why the edges were at 22%. On
   this map both qubits and pairs are controls, so it mounts with no highlight.
3. **The drawing carried every symbol it has** — resonator marks, flux stubs,
   coupler rings, direction arrows, C/T/M role labels, feedline buses, frequency
   chevrons, QDAC bias stubs — because on a component page the map IS the
   subject. Here only the stones and pair edges do anything, and the rest read
   as clutter over a table. Hidden, scoped to the pick mount. The list is
   derived from every `cm-` class `topo-graph.js` emits, not from what a
   screenshot happened to show: the first pass missed `cm-qdac`, and on this
   chip exactly one qubit has a QDAC bias, so a stub appeared under one stone
   and read as a rendering fault.
4. **The map's own click opens the entity in the inspector.** Here the click
   already means "show only its pulses", and doing both took the pane the table
   lives in — measured: the table dropped to ~220px with the inspector filling
   the rest, so the click a person made to narrow a list buried it instead. A
   pick mount's click belongs to the page that mounted it, and the hover card
   stops promising an inspector it will not open.

Also: the stone fill is **opaque** (an edge runs centre-to-centre, so a
see-through stone prints the line under the id), and this map keeps its **own**
collapse memory — one key for two roles would mean folding the filter away also
folds the component pages' drawing away.

Measured on the 20Q chip: 20 stones (40px), 30 edges, 340 → 300px tall, centred.
Click q1 → 16 rows, all `q1`, URL `?owner=q1`; click the q1-2 edge → 6 rows, all
`q1-2`; switch to the Z tab → pick kept; ✕ or clicking the same entity again →
back to 535. Light and dark both checked.

The picked entity FILLS rather than thickening its ring (2.2 → 3px is not a
state change anyone can see), and a page opened at `?owner=q1` renders the
chip and a real `href` fallback SERVER-side, so a filtered table always says
why it is filtered and how to leave — even before the map's code arrives.

Pinned by `tests/test_pulses_map_filter.py` (16 asserts, **14/14 mutations
red**) plus a `/pulse/row` 204 pin in `test_pulses_routes.py`.

---

## 4–5. The Calculator

**Reported**: the expression calculator at the bottom is a feature "많은 사용자가
존재 자체를 잘 모른다" — it was the last thing in the window, under a fold nobody
scrolled to. And the window should open as a menu, not already inside one task.

The expression box becomes the FIRST section, named **Simple Math Calculation**,
above "Power change → amplitude"; every section now starts closed. The ids are
untouched (calc.js finds every field by id, so they are the contract), and both
surfaces that render `_calc_body.html` — the in-page popover and the separate
`/calc-window` (docs/156) — get it from the one partial.

Verified in Chrome: six sections in order with Simple Math first, `openCount: 0`,
and `2^10` still evaluates to `1024`.

---

## A red pin this session's own earlier commit left behind

The closing full-suite run showed 15 failures. Fourteen reproduce identically on
a pristine worktree of `HEAD` — the documented docs/87 OS-behaviour class plus
the QDAC/gaussian live builds — and one, `TestEnvDiscoveryCache`, passes in
isolation (a full-suite flake).

The fifteenth was ours: `test_sidebar_run_rows.py::TestRowStyle` still asserted
docs/157's `--tree-entry-label-font: 1.06em` after docs/165 raised the rows to
`1.32em` at the customer's request, so it had been RED since that commit and
nothing had said so. The value moved for a stated reason, so the pin moved with
it — and what it is really guarding is now asserted as a RELATION (a date header
is never smaller than the rows beneath it; the badge and the name are each 1em
OF the row) rather than four literals that go stale together the next time
somebody asks for a bigger font. Both mutations red.

This is the docs/155 §10a lesson again: "pre-existing" is a measurement, not an
inference. The only way to tell was to run the same failures against a clean
`HEAD` in a separate worktree.

## What this round did NOT change

- The component pages' maps: every knob defaults to the pre-166 value and every
  new style rule is scoped to `[data-cm-pick="1"]`. `component_map_selfcheck`,
  `topo_graph_selfcheck` and `chip_status_hero_selfcheck` are green unchanged.
- The pulse row markup, the sparkline pipeline, the commit path.
- A pair pick shows that pair's own pulses only — not its two qubits' as well.
  That is what "그 qubit 혹은 pair의 pulse list만" asks for, and combining them
  would make the pick mean two different things depending on what you clicked.
