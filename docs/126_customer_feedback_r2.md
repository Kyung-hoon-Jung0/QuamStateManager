# docs/126 — Customer feedback round 2 (PJ_10082026 + topology/UX batch)

The second customer-feedback batch, received 2026-08-19. Scope confirmed with
the user before any code: ① the SUPER CRITICAL pair-grid search gap, ② the
Topology rework (metric patches drive the map), ③ Live-Edit patch bundle,
④ Json Tree bundle, ⑤ the apply-to-chip gate revision (user-directed covenant
amendment), ⑥ small UX items, ⑦ CosineBipolarPulse support + the Gaussian CZ
macro workflow. Reference chip: `D:\work\Customer_Codes\PJ_10082026\quam_state`
(READ-ONLY — a 20Q QDAC-biased tunable-coupler chip, 30 pairs).

## ① Pair grid: coupler/CR channel port-chain expansion (SUPER CRITICAL)

**Report.** "Typing `exponential` in Live Edit finds `exponential_filter`, but
the pair's flux line never shows up — the coupler side is unfindable. The Json
Tree finds it fine."

**Root cause.** `pair_columns._walk_pair` walked only the pair subtree's own
leaves; a channel's `opx_output` wiring POINTER stayed a raw pointer-string
column and the resolved port's leaves were never derived — unlike the qubit
grid, whose `qubit_columns._IO_KEYS` expansion has surfaced port leaves since
docs/94. On most chips that asymmetry was invisible (the same port is reachable
from some qubit's z), but this customer's chip is QDAC-biased (docs/119): 11 of
20 qubits have no OPX z at all and the COUPLER is the only entity wired to an
OPX flux port (`ports.analog_outputs.con1.5.*`). So the port filter leaves
(`exponential_filter`, `high_pass_filter`, `exponential_dc_gain`, …) existed on
NO grid, and the search — which docs/85 made whole-chip over the columns the
grid HAS — had nothing to find. Reproduced before the fix: qubit grid 1 column
(`dyn__z_opx_output_exponential_filter`, the 9 OPX-flux qubits), pair grid 0
port columns of 103.

**Fix.** `pair_columns` mirrors the qubit grid's expansion: `_IO_KEYS`
(`opx_output`/`opx_input`) pointers are not columns; `_port_leaves` resolves
the chain (`resolve_field_target` → port dict) and emits the port's scalar +
list leaves as `qubit_pairs.{pair}.<chan>.<io>.<leaf>` alias columns (labels
`out · <leaf>`), landing in the owning component's band. Everything downstream
already worked: cells build through the same `_build_bulk_cell` /
`_list_pair_cell` pipeline, list leaves open the shared ✎ JSON editor
(`BulkEdit.openJsonCell` has handled pair ▦ badges since r6 item 4), and the
pair search haystack is `label + key + section`, which now contains the leaf
names. Generic by construction — CR/ZZ channels' ports expand identically on
CR chips.

**Semantics kept:** all-null port leaves drop (same rule as every derived
column), nested port dicts (multi-DUC `upconverters`) never become columns,
a dangling wiring pointer yields no columns and no crash, and a mixed column
(7 pairs list-valued, 23 null on the real chip) renders per-pair — list rows
▦/✎, null rows scalar input — exactly as the qubit grid always has.

**Verified.** On the real chip: 9 port columns derived (111 total), path_map
addresses the alias with per-pair kind, and a full `/bulk` render carries all
9 in the pair-grid DOM (81 `coupler.opx_output.exponential_filter` cell
references). `test_pair_columns.py` (27) + `test_bulk_edit.py` (59) green;
`bulk_search_selfcheck.cjs` + `bulk_dyncols_selfcheck.cjs` green.

**Pinned by** `tests/test_pair_columns.py::TestCouplerPortChain` (port leaves
become columns; the pointer itself is not one; all-null dropped + nested dict
skipped; per-pair kind in path_map; dangling pointer degrades).

## ⑦a CosineBipolarPulse — catalog first-class + bit-exact preview

**Report (screenshot).** The PJ_10082026 chip's `cz_bipolar.flux_pulse_qubit`
rendered "Unrecognized pulse class `quam_builder…CosineBipolarPulse` — fields
are shown raw; the synthesized preview is unavailable."

**Why it was unrecognized.** quam_builder 0.4.0's `CosineBipolarPulse` is NOT
a renaming of the deprecated `_CosineBipolarPulse`/`SmoothedCosineBipolarPulse`
(different fields — explicit total `length`, no smoothing/padding — and a
different edge law: the non-flat remainder splits into rise/switch/fall
THIRDS). At docs/53 time it was deliberately left to the env-roster overlay —
the `env_creatable_specs` docstring literally used it as the example — and this
chip has no probed env attached, so nothing recognized 98 instances of it. Even
with a roster, `waveform_synth` had no transcription, so no preview either way.

**Fix.** Promoted to the static catalog (creatable, Flux/Bipolar; `qclass` IS
the quam_builder arch path — the class has no quam-era home, and docs/98
forbids writing a home no stack can import; `chip_qclass`'s majority-prefix
branch can never emit a legacy path for it since `_BY_QCLASS` holds only the
arch home) + `waveform_synth._cosine_bipolar` transcribing the env formula
exactly (halfcos rise → +flat → cosine switch → −flat → −halfcos fall; thirds
split with extra 1 → switch, extra 2 → rise+fall; even-flat and flat≤length
raises mirrored).

**Golden strategy.** The committed `waveform_golden.json` was generated on
quam 0.5.0a3 (LabC), which predates the class — regenerating it wholesale in a
modern env would break the deprecated-class cases in the other direction. So
generation-scoped goldens: `run_waveform_golden.py` gained
`--only-keys/--skip-keys/--out-name`, the new cases live in
`tests/golden/waveform_golden_qb04.json` (generated from the cqt env — quam
0.6.0 / quam_builder 0.4.0), the test fixture merges both files, and the
LabC live-regeneration test passes `--skip-keys CosineBipolarPulse`. Parity is
bit-exact (rtol 1e-9) over 7 cases including the customer chip's exact shape
(length 124 / flat 100 → 8/8/8 edges) and both raise paths.

**Ripples, all pinned.** The overlay/create tests that used CosineBipolarPulse
as *the* roster-only example now ride a synthetic `LabWigglePulse` (cloned
record at a lab home), and the promotion itself is pinned beside them:
`resolve_qclass` answers `exact` with no overlay, `env_creatable_specs(modern
roster) == {}`, the create page lists the class as a regular catalog entry
(env-verified, not env_only), and the two catalog-shape pins carry the one
deliberate qclass exception.

**Verified.** On the real chip: the screenshot's own pulse
(`qubit_pairs.q1-2.macros.cz_bipolar.flux_pulse_qubit`) resolves `exact` and
synthesizes 124 net-zero samples. 457 pulse-suite tests + 154 golden tests +
`pulses_create/commit` selfchecks green.

## ② Topology — the metric patches drive the map

**Report.** "Users want to press a patch (T1, T2echo, Qubit freq, Readout
freq, 1Q RB, 2Q RB…) and see the numbers appear inside the circles on the
MAIN topology immediately — not by scrolling down. Make the whole map ~2×
bigger; the pair edges are too small to even click; pairs should have a hover
panel too; the qubit panel's info is right but the styling is dated; dark
mode's axis labels are unreadable; delete Distributions."

**What shipped (all real-browser verified on the 20Q chip, 22 checks, 0
console errors — `tests/browser/_rt_cfb2_topo.cjs`, screenshots in
`_shots/cfb2-*`):**

- **Patch row**: `f_01` ("Qubit freq") and `readout_frequency` ("Readout
  freq") join the hero metric bar, and 2Q metrics become first-class **edge
  metrics** (`scope:'edge'`, ↔-prefixed): `cz_fidelity` ("2Q Bell") +
  per-edge-best `StandardRB`/`InterleavedRB` derived from the edges' own
  `gate_fidelities` with the same (0,1] physical gate as the Overview tiles —
  offered only when some edge has data (the real chip derives 2Q RB + 2Q IRB).
  Clicking any patch repaints the map in place; nothing scrolls.
- **Edge-metric mode**: edges are painted through the SAME chip-relative
  palette as the stones (per-render edge aggregate + `interpolateColor`),
  the value is printed ON the edge (`.topo-hero-eval`, paint-order-stroke
  halo — no pill-width estimation, readable over the colored line in both
  themes), stones go neutral (id only) so the edges read as the subject, and
  the legend switches to the edge gradient. Directed CR pairs offset their
  labels ±15 along the perpendicular.
- **Size**: the 70vh svg max-height letterbox is gone — `.topo-hero-scroll`
  owns overflow on both axes (max-height 82vh). The map renders at
  `zoom × pane width` with compact − / + / Fit controls (persisted,
  `quam_topo_hero_zoom`); the **default is computed as 2× what the old render
  actually showed** — `2·min(1, 0.7·vh·W/(H·paneW))`, because the old size
  was min(fit, 70vh-cap), so a tall chip needs LESS than 2× pane width to
  double (measured on the real chip: computed 1.084 ⇒ on-screen cell exactly
  2.0× the old; jsdom/unmeasurable falls back to a plain 2× pane). Two traps
  the real browser caught: Pico styles `[role=group]` as a full-width button
  bar (the zoom controls rendered as three 426px bars — role dropped) and
  Pico's global `button {width:100%}` (explicit `width:auto`).
- **Edges clickable**: node-metric-mode stroke +3 (5/6 vs the old 2.5/3.5),
  edge-metric-mode 9, and the invisible hit line 11 → **22**.
- **Pair hover popup** (`.topo-pair-popup`, reviving CSS that had been
  orphaned since the card diagram left in docs/120): per-gate 2Q fidelities
  (best-gate highlighted, measured-when recency chip), detuning, mutual flux
  bias, coupler decouple offset, moving-qubit role, confusion diag — same
  singleton/positioning/teardown as the qubit popup, exposed as
  `_sharedQubitPopup.openPair`.
- **Modernized popup chrome** (both popups, info untouched): 14px radius,
  layered shadow, translucent blurred surface (`color-mix` + backdrop-filter),
  theme-aware hairline borders (the old `rgba(0,0,0,…)` borders vanished on
  dark), round ghost close button, uppercase section titles, CZ/CR kind chip.
- **Dark mode**: the spec-driven charts (metric panels, 2Q RB) never set a
  font COLOR, so every axis label rendered Plotly's default gray —
  `_renderChartSpecsProgressively` now routes every layout through
  `PlotTheme.houseLayout` (deep-merged UNDER the spec's own sizes); verified
  619/619 dark-mode axis text nodes at `--plot-axis-text`. The hero's
  hardcoded `#666` null-cell text became `var(--pico-muted-color)`.
- **Distributions deleted** end to end: section + subnav + sidebar li +
  `_CHIP_VIEWS` + `_histDefs`/`renderHistograms` + observer selector + CSS.

**Pinned by** `chip_status_hero_selfcheck.cjs` part5 (freq patches + default,
zoom incl. persistence + jsdom fallback, edge-metric rendering + neutral
stones + persistence, 22 hit area, pair popup content) and the updated
`test_web.py` / `test_chip_trends.py` pins (8 subnav tabs, no distributions,
houseLayout at the spec choke point). One stale docs/125-era pin fixed along
the way: `test_beforeswap_plotly_purge_targets_swap_container` pinned the
variable NAME (`el`) the round-3 refactor renamed to `scope` — it now pins
the contract (docs/123 §8 class).

## ③ Live Edit — patches renamed, honest no-match, custom patches, faster press

**Reports.** "The Qubit-freq/amp patch is labelled `xy` — call it `qubit`";
"selecting XY then readout shows `No match` off at the right where nobody
sees it — put it front and center"; "(dangerous) pressing a patch and pressing
it again takes far too long"; "let users register their OWN patches — e.g.
`decouple`, or just `joint` for joint_offset — this would be really useful".

- **Label**: `_BULK_CHIP_TERMS`'s `("xy", "XY")` → `("xy", "Qubit")` (the term
  stays `xy` — it is what matches the section haystacks).
- **No-match band**: `#bulk-chip-offer` stays a child of the chipbar (the
  delegated yes/no click handling is untouched) but renders as a full-width
  CENTERED warning band (`flex: 1 1 100%` on a now-wrapping chipbar, warning
  palette) instead of the old right-corner note. Real-browser: 1282/1282 px
  wide, centered.
- **Custom patches** (`ChipBar` additions): a dashed `+` chip opens an inline
  input; the term is saved to `localStorage["quam_bulk_custom_chips"]`,
  injected as a dashed chip beside the server chips on every mount, and flows
  through the exact same `toggle/_write` path (so it filters BOTH grids and
  survives reloads); × removes it. One real bug caught by the pin work:
  `_removeCustom` must rewrite the search box BEFORE the term leaves `terms`,
  else `_freeTokens` keeps the word as user-typed text and the filter
  silently stays on (selfcheck F9).
- **Press latency**, CDP-profiled on the real chip (450-col qubit grid + 111
  pair): the shipped path did a full-table `querySelectorAll` AND a
  whole-table `PhysAmp.applyAll` PER COLD COLUMN it hydrated — pressing
  "Qubit" survives ~100 cold columns ⇒ ~200 full-table scans per press
  (measured 253 ms in querySelectorAll alone, 1.4–1.6 s wall). Two fixes:
  `_virtHydrateCols(keys)` batches the whole set into ONE cold-cell scan +
  ONE PhysAmp pass (all four hydration call sites route through it), and the
  search layer's ~9,000 per-td `classList.toggle` became ONE generated
  stylesheet (`#bulk-search-hide-style`; the ~460 THs keep the class — the
  count/offer/reveal machinery reads it there; cell Tab/arrow navigation
  consults the new `_searchHiddenKeys` set). Measured after: ON 1.6 s →
  0.9–1.1 s, OFF ~0.25 s. The residual is one full auto-layout of a
  460-column table — recorded as the structural limit (a fixed-layout /
  virtualized-table redesign, docs/104 class), not silently ignored.

**Verified** by `tests/browser/_rt_cfb2_chips.cjs` (real Chrome, real chip,
ALL OK: label, centered band geometry, add→filter→persist→reload→remove,
console clean). **Pinned by** `bulk_search_selfcheck.cjs` §F (F1–F9: inject,
inline add, active+box+store, filters like any chip, ONE-stylesheet td
hiding with THs keeping the class, × removal restores everything) and the
existing A–E pins green as the no-regression proof.

## ④ Json Tree View — patches, port owners, ⧉ copy

**Reports.** "Bring the patch idea to the Json Tree View too"; "mark which
qubit / flux / coupler each port belongs to (visible, simple)"; "on key hover
only edit and delete appear with an empty gap between — put a copy symbol
there that copies key + value".

- **Patches** (`window.ExplorerChips`): a chip row under the tree toolbar —
  curated terms rendered ONLY when they occur in this chip's documents (one
  lowercase JSON.stringify haystack; honesty rule carried over from Live
  Edit), plus the user's custom patches from the SAME
  `quam_bulk_custom_chips` store Live Edit writes — "decouple" registered on
  either surface serves both — with the same +/× add/remove flow. Click
  toggles the term in `#explorer-search` (space = AND, the docs/96 grammar)
  through `window.explorerSearch`; hand-typing a patch's word lights its chip.
- **Port owners**: `routes._port_owner_map` walks the wiring document's
  channel dicts (`wiring.qubits/qubit_pairs/twpas .<ent>.<role>.*`) and maps
  every `#/ports/...` pointer to `"<ent> · <role>"` (c→coupler, rr→readout,
  qt→trigger; shared ports list every owner). `/explorer` injects it as
  `window._treePortOwners` and `renderJsonTree` hangs a muted `⌁ q2 · z`
  chip on any container node whose dot path is in the map — 69 ports labeled
  on the real chip, zero invented (an unshaped wiring entry yields nothing).
- **⧉ copy**: `_buildRowActions` now gives EVERY hovered row (list elements
  and `__class__`/`id` rows included — they used to get no actions at all) a
  ⧉ button that puts `"<key>": <JSON value>` on the system clipboard
  (navigator.clipboard with an execCommand fallback, ✓/✗ feedback). Distinct
  from the in-app paste buffer (`_treeCopyKey`), which keeps its dblclick.

**Verified** by `tests/browser/_rt_cfb2_tree.cjs` (real Chrome, real chip,
ALL OK: chips honest + filter 1,875 rows hidden/2,212 highlighted, patch saved
in the tree appears in Live Edit, `⌁ q2 · z` on the port node, clipboard got
`"octaves": {}`, console clean). **Pinned by**
`tests/explorer_chips_selfcheck.cjs` (19 checks) + `tests/test_explorer_features.py`
(owner-map derivation incl. shared ports and broken shapes; route injection).
Harness note for the future: `jsonTreeSearch` debounces 200 ms — a pin that
reads the tree earlier measures the pre-search state.
