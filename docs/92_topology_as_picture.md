# 92 — Chip topology as a picture (implements the docs/91 plan)

*2026-08-09. Ships the whole docs/91 plan — P0 honest-layout core, P1 Chip
Status hero map, P2 shared component layout + Pairs, P3 the remaining four
highlight layers — plus two pre-existing server defects the work exposed.
Read docs/91 for the design argument; this file records what shipped, what
was found, and what is pinned.*

## 0. The two reports, answered

① *"Chip Topology is old and text-only"* → the Chip Status topology section is
now led by **one SVG hero map** (docs/91 §3.1): nodes at real positions, the
selected metric's **value printed on every node** (this surface *integrates
numbers* — §2.4), edges coloured by CZ state, legend with a no-data colour
visibly distinct from "bad". The property-card diagram it modernizes is
**untouched below it** (all data stays always-visible — the standing house
preference).

② *"Components pages hold topology and show none of it"* → every component
page (Qubits / Pairs / Resonators / Flux / Couplers) mounts **ONE shared
no-numbers drawing** above its untouched table (§3.2): all component symbols
always present, the page only changes **what lights up** (§3.3). Map↔table
hover binds both ways off the rows' existing `data-qubit-id`/`data-pair-id`
attributes — the templates' tables were not restructured.

## 1. P0 — `TopoGraph.layoutFor`, the honest-layout core (§2.1)

`layoutFor(nodes, edges)` → `{mode, positions, cols, rows}`; **three states,
never two**:

| mode | when | positions |
|---|---|---|
| `physical` | EVERY node passes the strict grid gate (`normalizeGrid` strict — layoutFor adds **no new gates** over it: duplicate declared cells stay physical, they are the chip's own claim) | the chip's own, 0-based, row-flipped |
| `logical` | none/partial grid, but pairs exist | derived from pair connectivity ALONE — partial grids are **ignored entirely** (a half-fabricated map is still fabricated). Deterministic Fruchterman-Reingold (circle init by input order, fixed 300 iterations, principal-axis align, mirror normalize, median-edge rescale to 1 cell, short overlap relax) + degree-0 nodes on a **separate strip below** (they have no connectivity information, so they get no implied neighbours). CR's anti-parallel duplicate pairs dedupe; edges accept both `{source,target}` objects and `[a,b]` arrays |
| `none` | no complete positions AND no usable pairs | none — the caller renders one honest line, never an empty box or a raster |

The tolerant `(i%4, i/4)` raster is **never promoted to a map mode** — it
remains display-only decoration where it always lived (the property-card
fallback). `LOGICAL_LAYOUT_NOTE` ("Logical layout — this chip declares no
usable physical positions") is exported from the module so every surface
shows the SAME wording, **on the map itself** (§6.4 — the label is the
load-bearing part). Pure, no DOM, byte-identical output for identical input
(no `Math.random`, fixed iteration counts).

Verified on **8 real chips** (21Q CZ + CR, 17Q ×2, 9Q, 3Q, a 21Q variant, a
10Q) — all as-declared physical; **forced-logical runs over the six real pair
graphs** (grids stripped, real connectivity through FR): every node placed,
min pairwise distance ≥ 0.7 cells, edge lengths within [0.7, 1.5] cells,
determinism re-checked on real data. No chip in the local archives declares
a missing grid today, so the no-grid case rides the synthetic selfcheck cases
plus those real-connectivity stripped runs — stated here honestly rather than
claimed as a real-file observation. One real **data finding**: a 10Q chip
declares two qubits at the same cell (`"4,0"`) — which is why coincident
DECLARED cells are first-class: `spreadCoincident` (also exported, the ONE
fan-out shared by hero + component maps) fans shared-cell members around the
shared centre so **both stay visible** — a visual de-overlap at the same
cell, never a fabricated new cell; members wear a dashed ring.

## 2. P1 — the Chip Status hero map

Client-side in `chip-status.js` (`buildHeroMap`, mounted at `#topo-hero`
ahead of the card wrap — server pin: the mount precedes `#topo-html-wrap`).
Geometry/mode from `layoutFor`; painting is Chip-Status-owned because this is
the surface that carries numbers (§2.4):

- **Metric selector** (persisted `quam_topo_hero_metric`): T1 · T2echo ·
  1Q gate fidelity · readout (assignment) fidelity · last-calibrated age ·
  open diagnostics. Continuous metrics ride the **same** `_mv` physical gate +
  `propBgColor` chip-relative normalization + the user's active heatmap
  palette as every other surface — and a palette switch re-renders the hero
  (`switchPalette` hook), so the map can never disagree with the cards below
  it. Age + diagnostics ride the app's pass/warn/fail status tokens
  (`_ageClass`, per-qubit `jump_path`-attributed finding counts).
- **Edges** go through `_edgePaint` — extracted as the ONE source both the
  card diagram and the hero read (the selfcheck pins the two surfaces'
  stroke multisets equal). CR pairs keep the anti-parallel offset so both
  directions show; inactive pairs dim.
- **Honesty**: a missing value renders the no-data colour + "—" (never the
  low end of the scale); an unphysical fit (`_badFit`) renders ringed dashed
  + struck-through, never a heat colour; logical mode wears
  `LOGICAL_LAYOUT_NOTE` as an on-map banner; mode `none` renders one honest
  line while the cards below still carry the data.
- **Interactions** reuse what exists: hover opens the SAME qubit popup
  (property rows + Param-History sparklines) through a small bridge handle
  buildTopology now exposes — never a second popup implementation; click
  inspects (`/qubit/<id>`), double-click opens the wiring-JSON panel; edge
  click inspects the pair.

## 3. P2/P3 — the shared component layout

`TopoGraph.renderLayout(mount, {nodes, edges, highlight})` is the shared
drawing (§2.2 — extending the module all four existing surfaces already
agree through, not a renderer beside it): qubit stones + ids, pair edges
(CR arrows, coupler dots on `has_coupler` edges), resonator marks (NE of the
stone), flux stubs (S), and **feedline buses** — nodes sharing one `rr_port`
label share the physical readout line, drawn as a thin dashed bus through
their resonator marks. **The only text in the drawing is the qubit id** —
pinned: every rendered `<text>` is a bare node id (§2.4, numbers live in the
table). Highlight is a class on the svg root (`cm-hl-<mode>`); CSS owns the
dimming, and the selfcheck pins the drawings **byte-identical across modes**
up to that class — emphasis never content (§3.4). Per §3.3: qubits → stones;
pairs → edges; resonators → marks + feedline buses; flux → stubs with pair
edges kept mid-bright as neighbour links (crosstalk candidates); couplers →
dots + the edges that have them.

`window.ComponentMap` (component-map.js) mounts it from `_component_map.html`
(included by all five `_*.html` partials above their tables — the tables
untouched): fetches `/api/topology` (cached server-side per store), default
open with the collapse choice persisted under ONE key for all five pages
(`quam_component_map_open` — it is one picture), lazy when closed, and owns
the map↔table binding: hovering a row lights the entity, hovering an entity
lights the row, clicking an entity opens its inspector. `#table-pane` is the
HTMX swap TARGET and persists across swaps, so it carries ONE delegated
hover listener reading the CURRENT mount through a module handle —
re-mounting on every swap can never stack handlers (pinned: after a
simulated swap + re-mount, exactly one entity lights).

## 4. Two pre-existing server defects the work exposed (both fixed)

1. **`_extract_port_label` returned None on every fully-resolving chip.** It
   expected resolution to stop at the wiring reference STRING
   (`#/ports/mw_outputs/con1/1/1`), but on real chips the pointer chain
   (state → wiring → ports) resolves all the way to the **port dict** — so
   `xy_port`/`rr_port`/`z_port` were None for every real chip and nobody had
   noticed because the feedline grouping is their first real consumer. Fixed:
   a dict result builds the label from the port's identity fields
   (`controller_id`/`fem_id`/`port_id`); a string result keeps the old
   parser (dangling/partial refs). After the fix, all four verified real
   chips label 21/21 (or 13/13) ports and group into real feedlines —
   e.g. the 21Q chip's four buses of 6+5+5+5 resonators, the 17Q's three of
   4+5+4. This is exactly the §2.5 instrument-space case the user named.
2. **An explicitly-null channel 500'd the whole topology.** `get_topology`
   read `q.get("z", {})` — `dict.get` falls back only when the key is
   *missing*, not when it is None, and trimmed real chips carry
   `"z": null` (docs/72). `z.get("flux_point")` → AttributeError → every
   Chip Status surface dead. Fixed with `or {}` on all four channel reads
   (same class as the r16 `/pairs` `is number` fixes; found by the new
   port-label test's null-z fixture, kept as a pin).

Additive server change: `get_topology` edges gain `has_coupler`
(dict-valued coupler counts, explicit null does not — `get_pair`'s existing
convention).

## 5. Verification ledger (docs/90 §4 grading)

| Claim | Strongest evidence | Grade |
|---|---|---|
| layoutFor honesty modes + logical layout quality | 8 real chips as-declared + 6 real pair graphs forced-logical (all placed, spacing/edge-length bounds, determinism); synthetic partial/none cases | **A — real data** |
| Port labels + feedline grouping | 4 real chips: 21/21 · 21/21 · 13/13 · 21/21 ports labelled; feedline groups 6+5+5+5 / 4+5+4 match the chips' wiring | **A — real data** |
| Hero map end-to-end | **Real browser** (Chrome, dark theme) on the real 21Q chip: 21 nodes + 31 edges rendered, values on nodes, metric switch (T1 → age) live, no-data node renders "—", zero console errors | **A — real browser, real chip** |
| Component maps end-to-end | Real browser: Pairs (edges lit over dimmed stones, 31-row table intact), Resonators (4 feedline buses visible, both hover directions verified live in-page), qubits/flux/couplers mounts + modes verified | **A — real browser, real chip** |
| Edge-colour parity hero↔cards | Selfcheck: stroke multisets equal (one `_edgePaint`) | jsdom |
| No-numbers contract, mode-emphasis-only, swap-safety, collapse persistence | `component_map_selfcheck.cjs` against the real shipped JS | jsdom |
| The four legacy `renderStatic` consumers | `topo_graph`/`generate_czorder`/`generate_topoboard`/`chip_status_edge_orientation`/`compare_hub` selfchecks all green after the module extension | regression pin |

Not verified: no real chip in the local archives lacks `grid_location`, so
logical mode has not been seen over a real *file* (only real connectivity
with grids withheld); coupler dots have not been seen on a real
tunable-coupler chip (none local — the test fixture covers the field).

## 6. Pins

`tests/topo_graph_selfcheck.cjs` (+`test_topo_graph.py`) — layoutFor's three
modes incl. partial-never-physical, raster-never-emitted, isolated strip,
determinism, edge-shape/duplicate tolerance, LOGICAL_LAYOUT_NOTE contract.
`tests/chip_status_hero_selfcheck.cjs` (+`test_topology_hero.py`) — hero
honesty modes, values-on-map, edge parity with the cards, bad-fit rendering,
coincident fan-out, metric switch persistence, cards-still-built, the
server mount-order pin. `tests/component_map_selfcheck.cjs`
(+`test_component_map.py`) — the drawing inventory, the no-numbers pin,
emphasis-only, note/none honesty, both hover directions, swap-safety,
collapse persistence + laziness; server pins for all five pages' mounts +
row hooks, `has_coupler`, and the port-label/null-channel fixes.

## 7. Deliberately not done

- No chart library; hand-rolled SVG through the existing module (docs/91 §7).
- Chip Status stays read-only — the Generate board owns editing.
- No numbers in the component map, ever (§2.4) — colour means selected/not.
- The feedline bus routes are first-render simple (sorted along the dominant
  axis); §6.6 says tune against real renders — revisit if a lab reports a
  confusing route, with the real chip on screen.
