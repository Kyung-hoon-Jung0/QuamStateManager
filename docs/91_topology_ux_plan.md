# 91 — Chip topology as a picture (PLAN, not shipped)

*Status: **PLAN ONLY** — nothing in this document is implemented. Written
2026-08-09 to be picked up in a fresh context alongside
`docs/90_merge_audit_handoff.md`. Read §1–§3 before writing any code.
**Every open question is answered (§6) — this is ready to implement as written.***

---

## 0. The two reports

> **①** Chip Topology의 design이 너무 old하고 text로만 있다고 피드백을 받았어.
> 좀 더 modern하게 디자인 할수는 없을까? 가지고 있는 정보력 자체는 좋다고 해.
>
> **②** 현재 Components에서 qubits, pairs, resonators, flux, coupler 등의 tab을
> 들어가면 table view만 나오는데, 이것도 어째서 topology 정보를 가지고 있으면서
> 표시를 하지 않은거냐는 피드백이 많았어. 각각의 component가 어디에 존재하는지
> display를 그림으로 나타내보자.

Both are fair, and ② is verified fact: the five component templates contain
**zero** `<svg>`, `<canvas>` or topology markup (`_qubits.html` 141 lines,
`_pairs.html` 211, `_resonators.html` 124, `_flux.html` 122, `_couplers.html`
114 — all table). The app computes a full topology and then shows none of it on
the pages where entities are actually inspected.

---

## 1. What already exists (inventory — verify before changing)

| Thing | Where | Note |
|---|---|---|
| **`window.TopoGraph`** | `web/static/topo-graph.js` (264 lines) | **THE shared convention module.** Owns `quamPairId`, `normalizeGrid` (incl. the row-flip), `legendForGate`, `renderStatic`. Framework-free IIFE. |
| Chip Status dashboard | `web/static/chip-status.js` (2,485 lines) | Heatmaps, overview tiles, verdict line. Already has a colorscale. |
| Health model | `core/chip_health.py` (357 lines) | |
| Topology data | `core/query.py::get_topology()` → `{nodes, edges}` | Also `GET /api/topology` (`routes.py:13611`) |
| The page | route **`/topology`**, page token `topology`, sidebar label **"Chip Status"** | `routes.py:7108` `_CHIP_VIEWS = {topology, overview, distributions, gate, fidelity, …}` |
| Existing consumers of `renderStatic` | Generate board (editable), Populate step, Chip Status, Compare hub (`compare-hub.js:423`) | Four surfaces already agree via this one module |
| Component pages | `/qubits` `/resonators` `/flux` (`routes.py:3751/3836/3842`), `/pairs` `/couplers` (`6254/6326`) | table-only |

**Conclusion: this is mostly a reuse job, not a new-renderer job.** That matters
— see §2.2.

---

## 2. Constraints I would not negotiate

### 2.1 A map that invents positions is worse than a table

`normalizeGrid` has two modes, and the **tolerant** one (the property-card path)
falls back to `(i % 4, floor(i / 4))` when a node has no usable
`grid_location` — and returns `placed: true` regardless. Real chips return `""`
grid_location freely.

A researcher reads **adjacency** off a chip picture. If we draw a 4-wide raster
for a chip that never declared positions, we are asserting neighbours that do
not exist, and they will believe it. That is a worse failure than showing
nothing.

So: **three honest states, never two.**

| chip declares | render | label |
|---|---|---|
| valid `grid_location` for every node | physical map | (none needed) |
| none / partial | **connectivity** layout derived from `qubit_pairs` only | **visibly labelled on the map** — *"logical layout — this chip declares no physical positions"* |
| no positions **and** no pairs | no map; keep the table | one honest line, not an empty box |

Row 2 is **decided** (§6.4): draw it, and make the label unmissable — on the map
itself, not in a tooltip or a legend that can go unopened.

The existing `strict` mode already encodes exactly this distinction
(`placed:false` unless *every* node has a valid grid_location) — use it, and
treat `tolerant` as display-only decoration, never as the basis for a map that
implies physical neighbours.

**This is the single highest-risk item in the whole plan.** Whoever implements
it should write that test first.

### 2.2 Do not build a second renderer

`topo-graph.js` exists precisely so the Generate board, Populate, Chip Status
and Compare cannot disagree about where qubit 5 sits or what a pair is called.
Adding a component-page renderer beside it re-opens that seam. Extend
`renderStatic` (or add a sibling that shares `normalizeGrid`/`quamPairId`)
instead.

Precedent in this codebase: docs/84 reused `renderJsonTree` rather than writing
a diff tree, and docs/85 reused the existing hint chip rather than adding a
second one. Both are noted as the reason those changes were small.

### 2.3 The map must not replace the table — CONFIRMED by the user

> 표를 없애자는게 절대 아니야! 표는 반드시 있어야지. 표 위에 렌더링을 해서,
> 전체적인 component 맵을 보여주자는거야.

The tables are dense and people use them for numbers. The picture answers
*where / which neighbours / which feedline*; the table answers *what value*.
Ship both. The map is **added above** the table; the table is untouched.

### 2.4 The division of labour that makes both maps easy

This is the user's framing and it is the key design idea in this document:

> Chip topology에서는 전체 그림을 수치들과 함께 보여주니까 layout하기가 힘들지만,
> components는 일단 수치들을 표로 빼놓고 보니까, 그림만 그려도 충분하거든?

| surface | carries numbers? | consequence |
|---|---|---|
| **Chip Status map** | **yes** — T1 / T2 / fidelity, integrated | layout is hard *because* values must coexist with position. This is what it is FOR, and it keeps its own meaning. |
| **Component pages map** | **no** — the table beside it has every number | the drawing can be pure symbols, so it can be clean, dense and small |

So they are not two attempts at the same picture. Getting the numbers out of
the component map is what lets it be good.

### 2.5 "Where does it exist" means BOTH — answered

- **Chip space** — position on the die (`grid_location`, pairs).
- **Instrument space** — which controller / FEM / port drives it, and *which
  components share* one (feedline multiplexing, LO-coupled port pairs).

Originally raised as an open question; the user answered it by naming the case
directly — "edge나 **readout feedline**등을 보면서 동시에 표를 볼수있다". So the
shared layout carries both: die position as the geometry, and instrument
grouping (feedline, shared port) as a highlight layer over it. No separate
instrument-space view is needed.

---

## 3. Proposed shape

### 3.1 ① Chip Status topology — modernize

Keep every number it already shows (the report says the information is good).
Change the presentation:

- **One SVG chip map as the section's hero**, not a text block: nodes at real
  positions, edges for pairs/couplers, node fill = the selected metric.
- **A metric selector** driving the fill (T1 · T2E · gate fidelity · readout
  fidelity · last-calibrated age · "has an open diagnostic"). Reuse
  `chip-status.js`'s existing colorscale so the app agrees with itself.
- **A legend that shows the scale AND the no-data colour**, visibly distinct
  from "bad" — a missing T1 must never look like a short T1.
- Hover → the existing sparkline popover (`routes.py:10233
  /topology/sparklines/<qubit>` already exists — reuse, don't reinvent).
- Click a node → open that qubit's inspector (existing route).

Deliberately **not** proposed: 3-D, animation, drag-to-rearrange (the Generate
board owns editing; Chip Status is read-only), or a chart library. SVG at
17–50 qubits is trivial and the app already ships Plotly for the things that
need it.

### 3.2 ② Component pages — ONE shared layout, selection highlights it

The user's model, which replaces an earlier and worse proposal (five different
pictures, node fill driven by the table's sorted column — see §3.4):

> 1. 전체적인 공유 layout을 그린다. components들의 symbol들이 그려진 채로.
>    수치는 없이.
> 2. Qubits, Resonators, pairs등을 사용자가 선택할때마다 그 공유 layout에서
>    highlight되는 부분이 음영으로 바뀌면서 highlight된다.

So:

* **One drawing, drawn once.** The same chip layout appears above the table on
  every component page, always showing every component type's symbols — qubit
  nodes, resonators, couplers, flux lines, pair edges. It does **not** change
  shape between pages.
* **Selection changes emphasis, not content.** Opening *Pairs* dims everything
  and lights the pair edges; opening *Resonators* lights the resonators and
  their shared feedline grouping; and so on. Nothing appears or disappears —
  the user keeps one stable mental picture of the chip and just looks at a
  different layer of it.
* **No numbers anywhere in it** (§2.4). Symbols and highlight only.
* The table below is unchanged, and the two are bound: hovering a row lights
  that entity in the map, hovering an entity lights the row.

Why this is better than what I first proposed: a single layout is **learnable**
(you build one mental model instead of five), it is far less code (one renderer,
one layout pass, a highlight layer), and it makes cross-component questions
answerable *without changing page* — you can see, while reading the Pairs table,
that two of those pairs sit on qubits sharing one readout feedline.

### 3.3 What lights up per selection

Same drawing throughout; only the highlight layer changes.

| page | highlighted | dimmed to context | the point |
|---|---|---|---|
| **Qubits** | qubit nodes | everything else | where each qubit sits |
| **Pairs** | **pair edges** | nodes, other components | adjacency — a table cannot show this at all |
| **Resonators** | resonators + **shared-feedline grouping** | qubit grid | *which resonators share a readout line* — invisible in the table today, and the thing that explains readout crosstalk |
| **Flux** | flux lines + neighbour links | qubit grid | which lines are adjacent ⇒ crosstalk candidates |
| **Couplers** | **coupler edges** | nodes | same argument as Pairs |

Resonator→feedline remains the strongest single case in this document: the app
already holds it (state→wiring→`ports.mw_outputs.*` chains) and shows it
nowhere. The user named it directly ("readout feedline등을 보면서").

### 3.4 Dropped from the first draft

* **"The table's sorted numeric column drives node fill."** Dropped. It smuggles
  numbers back into a map whose whole advantage is not having them (§2.4), and
  it would make the component map compete with Chip Status instead of
  complementing it. Colour in the component map means *selected / not selected*,
  nothing else.
* **"Each page gets a different picture."** Dropped in favour of one shared
  layout with a highlight layer.

## 4. Suggested phasing

Each phase is independently shippable and independently revertible.

- **P0 — the honest-layout core.** Extend `topo-graph.js` with a
  `layoutFor(nodes, edges)` that returns `{mode: 'physical'|'logical'|'none',
  positions}` implementing §2.1. Pure, no DOM. **Test first**, including a chip
  with no `grid_location` and a chip with partial. *No UI change in this phase.*
- **P1 — Chip Status hero map** (§3.1) against the real chips.
- **P2 — the shared layout + ONE page's highlight** (recommend **Pairs**:
  adjacency is the clearest win and the edge-case set is smallest). This phase
  builds the whole drawing — every component type's symbols — and one highlight
  layer over it, plus the map↔table binding (§3.2).
- **P3 — the remaining four highlight layers.** Cheap by construction: the
  layout already exists, each page adds only *what lights up* (§3.3). Resonators
  brings the feedline grouping (§2.5) and is the one with real new data behind
  it, so do it first of the four.

Do not start P2 before P0's tests are green; every later phase inherits the
layout honesty from it. There is no P4 — the instrument-space view folded into
the shared layout (§2.5) instead of becoming its own surface.

## 5. Verification expectations

Following this repo's standard (see `docs/90` §4 for how prior work was graded):

- **Real chips, not fixtures**, for anything claiming a layout is correct. Use
  several with different shapes — including at least one that declares **no**
  `grid_location`, which is the case §2.1 is about.
- A **jsdom selfcheck** driving the real `topo-graph.js` for the layout modes
  and the map↔table linking (`tests/*_selfcheck.cjs` + a pytest driver is the
  house pattern).
- **Server-render pins** that each component page mounts the map container and
  that the existing tables are unchanged.
- A **regression pin on the four existing `renderStatic` consumers** (Generate
  board, Populate, Chip Status, Compare hub) — extending the shared module is
  the main way this change could break something far away.
- **Take a real browser look** this time if one is available. `docs/90` §4 flags
  that no browser check was possible for the last chain; a visual feature is
  where that gap costs the most.

## 6. Questions — ALL ANSWERED (2026-08-09)

Nothing in this plan is blocked on the user. Implement it as written.

1. **Which surface is "Chip Topology"?** → the Chip Status map, and it **keeps
   its own distinct purpose**: *"T1, T2, Fidelity등의 수치를 통합적으로 보여주는
   맵"*. Not superseded by the component map; §2.4 is the division of labour.
2. **Instrument space too?** → yes, folded into the shared layout as a highlight
   layer (§2.5), not a separate view. (This is why there is no P4.)
3. **Does the plan still cover modernising Chip Topology itself?** → yes, §3.1.
   It is half the work, not a side effect of ②.
4. **Chips with no `grid_location`** → **draw the logical layout, and label it so
   the user knows.**
   > ④ 논리 레이아웃 라벨 붙여서 그리자 다만, 사용자가 알수있게 표시는 해야해.

   So the middle row of §2.1's table is the chosen behaviour, and the *label is
   the load-bearing part*: it must be visible on the map itself, not only in a
   tooltip or a legend the user may never open. A logical layout that reads as
   physical is the failure this plan exists to prevent (§2.1). Suggested wording
   on the map: **"logical layout — this chip declares no physical positions"**.
   Test it as a first-class case in P0.
5. **Map default open or collapsed?** → **open.** The whole report is that people
   cannot see this information at all. Collapse state still persists per house
   convention (`localStorage`), so anyone doing bulk numeric work can reclaim the
   space and keep it reclaimed.
6. **How much of the layout is "every component type"?** → **proceed as
   proposed**: draw all of them, let the non-highlighted layers dim far enough to
   read as background texture rather than content, and revisit after seeing P2 on
   a real chip. If a 50-qubit chip turns out to be crowded, that is a tuning
   decision made against a real render, not in advance.

## 7. What NOT to do

- Do not let the map fabricate positions (§2.1).
- Do not add a charting dependency; everything here is hand-rolled SVG plus the
  existing module. The frontend is deliberately framework-free.
- Do not remove or restructure the existing tables — this is additive.
- Do not make Chip Status editable; the Generate topology board owns that.
