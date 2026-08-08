# 91 — Chip topology as a picture (PLAN, not shipped)

*Status: **PLAN ONLY** — nothing in this document is implemented. Written
2026-08-09 to be picked up in a fresh context alongside
`docs/90_merge_audit_handoff.md`. Read §1–§3 before writing any code.*

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
| none / partial | **connectivity** layout derived from `qubit_pairs` only | visibly labelled *"logical layout — this chip declares no physical positions"* |
| no positions **and** no pairs | no map; keep the table | one honest line, not an empty box |

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

### 2.3 The map must not replace the table

The tables are dense and people use them for numbers. The picture answers
*where / which neighbours / which feedline*; the table answers *what value*.
Ship both, **linked** — that is where the actual UX win is (§3.2), not in the
drawing itself.

### 2.4 "Where does it exist" is ambiguous — and both answers are useful

- **Chip space** — position on the die (`grid_location`, pairs). Almost
  certainly what the report means.
- **Instrument space** — which controller / FEM / port drives it, and *which
  components share* one (feedline multiplexing, LO-coupled port pairs).

The second is invisible in every current table and is arguably the higher-value
one for resonators (§3.3). Plan primary = chip space, secondary = a port/feedline
strip. **Open question for the user in §6.**

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

### 3.2 ② Component pages — map + table, linked

Every component page becomes: **map on top, existing table below**, with the two
bound in both directions.

- hover a row → the corresponding node/edge highlights on the map
- hover a node → the row highlights and scrolls into view
- click a node → filters the table to that entity (click empty space clears)
- the map's node fill follows the table's **currently sorted column** when that
  column is numeric — so sorting by T1 recolours the chip by T1, for free

That last one is the idea I would fight for: it makes the picture and the table
the *same* view instead of two panels, and costs almost nothing because the
table already knows its sort column.

Collapsed state persisted in `localStorage` (house convention, e.g.
`quam_topo_map_collapsed`), because someone doing bulk numeric work will want
the space back.

### 3.3 What each page's map should actually show

They are **not** the same picture, and this is where the design earns its keep:

| page | nodes | edges | the point |
|---|---|---|---|
| **Qubits** | qubits | pairs (thin, context) | where each qubit sits |
| **Pairs** | qubits (dim) | **pairs (primary)** | adjacency — a table cannot show this at all |
| **Resonators** | resonators | **shared feedline groups** | *which resonators share a readout line* — invisible in the table today, and the thing that explains readout crosstalk |
| **Flux** | qubits | flux-neighbour links | which lines are adjacent ⇒ crosstalk candidates |
| **Couplers** | qubits (dim) | **couplers (primary)** | same argument as Pairs |

Resonator→feedline is the strongest single case in this document: it is real
information the app already holds (state→wiring→`ports.mw_outputs.*` chains) and
currently shows nowhere.

---

## 4. Suggested phasing

Each phase is independently shippable and independently revertible.

- **P0 — the honest-layout core.** Extend `topo-graph.js` with a
  `layoutFor(nodes, edges)` that returns `{mode: 'physical'|'logical'|'none',
  positions}` implementing §2.1. Pure, no DOM. **Test first**, including a chip
  with no `grid_location` and a chip with partial. *No UI change in this phase.*
- **P1 — Chip Status hero map** (§3.1) against the real chips.
- **P2 — one component page end-to-end** (recommend **Pairs**: adjacency is the
  clearest win and the edge case set is smallest), including the linking in §3.2.
- **P3 — the remaining four**, reusing P2's component wholesale.
- **P4 — the feedline/port view** (§3.3 Resonators, §2.4 instrument space), only
  if the user confirms it is wanted.

Do not start P2 before P0's tests are green; every later phase inherits the
layout honesty from it.

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

## 6. Open questions for the user

1. **Which surface is "Chip Topology"?** Best guess: the `topology` section of
   the Chip Status dashboard (route `/topology`, sidebar "Chip Status"). Confirm
   before P1 — there is also a topology tab inside chip-compare.
2. **Instrument space too?** (§2.4) — is "어디에 존재하는지" the die position, or
   also which FEM/port/feedline drives it? The plan assumes die position first.
3. **Map default: open or collapsed** on the component pages? (Recommend open;
   the report is that people cannot see the information at all.)
4. **Chips with no `grid_location`** — is the connectivity ("logical") layout
   wanted, or should those chips simply keep the table? (Recommend the labelled
   logical layout; §2.1 forbids the silent fake either way.)

## 7. What NOT to do

- Do not let the map fabricate positions (§2.1).
- Do not add a charting dependency; everything here is hand-rolled SVG plus the
  existing module. The frontend is deliberately framework-free.
- Do not remove or restructure the existing tables — this is additive.
- Do not make Chip Status editable; the Generate topology board owns that.
