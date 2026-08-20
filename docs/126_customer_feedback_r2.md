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

## ⑤ Apply to chip — the identity gate is the only question left

**Report + ruling.** "Apply to chip is awkward — make it just happen in one
go." Discussed before any code (the user's explicit instruction): *"except
when it looks like a DIFFERENT chip, just always do it. Don't worry about
live drift — the user pressing this button already knows the chip moved and
wants to revert it; that is WHY they are pressing."* This amends, for THIS
button only, docs/65's "a staged conflict never force-pushes" and docs/116's
in-place conflict panel. The covenant floor (docs/107/117) did not move — the
press is still the one explicit act; what changed is that the press now
answers the staleness question too.

**What shipped.**
- `_sync_pull_apply_to_live` gained a `force` passthrough (the ⚡ tray path
  at `/state/apply-to-live` already had its own).
- `/dataset/<uid>/load-state?apply=1`: the **pending-edit 409 is gone on the
  apply path** — the working copy is replaced and the result line reports
  "(Replaced N unsaved edits.)" (docs/86: reported, never silent). The plain
  **Stage only** path keeps its 409 byte-identically — review-first still
  reviews.
- A **staleness drift no longer raises the conflict panel**: the first
  attempt stays unforced (that failure is what tells us drift existed), the
  retry forces, and the result line names it — "The live chip HAD changed
  since it was loaded — those changes were overwritten (the run's state
  wins)." The docs/116 identical-content carve-out stays the quiet path, so
  the note appears only when something real was replaced. ↺ Revert last
  apply is armed either way (asserted in the pin) — reversibility is what
  licenses all of this.
- The **identity gate is untouched**: a different/unverifiable chip still
  409s, still carries `apply=1` through its confirm.
- `_ds_apply_conflict.html` (docs/116) is deleted — nothing renders it.

**Pinned by** the rewritten `tests/test_dataset_apply_to_chip.py` (10):
identity-only gate, pending-edits reported not asked (with the stage path's
409 in the same test as the contrast), drift overwritten + named + revert
armed, real-difference disclosure vs identical-content quiet, stage-only
byte-identical, one-call apply. `test_state_roundtrip` + `test_overwrite_live`
+ `state_sync_selfcheck.cjs` green as proof the OTHER surfaces' gates did not
move.

## ⑥ Small items — six fixes, real-browser verified in one probe

All six verified together in `tests/browser/_rt_cfb2_misc.cjs` (real Chrome,
real chip + the real run archive, 20 checks, console clean); pinned by
`tests/test_misc_ui.py`.

1. **Floating Instrument Wiring** (`window.FloatWiring` + the ⧉ button beside
   the sidebar item): a body-level panel rendering the SAME
   `renderInstrumentWiring` rack from the new read-only
   `GET /api/instrument/data` (honest error payloads, never an empty rack).
   Draggable by its header, collapsible to the title bar, CSS-resizable —
   position/size/collapse persisted (`quam_float_wiring`); hover details land
   in the panel's own footer strip via the renderer's existing `onPortHover`
   hook (the cursor popup + JSON drill-down stay with the main page, whose
   `_showInstrumentJsonPanel` already no-ops without its mounts). Survives
   htmx pane navigation (verified: 242 ports drawn on /bulk, hover
   "q1.rr · rr", drag+collapse persisted). Refreshes itself on
   `stateRestored` (chip switch / stage / pull).
2. **Calculator pin removed** (customer: meaningless): button, handler and
   CSS gone. The intent it served moved to the gesture that actually
   communicated it — a DRAGGED (`.calc-floating`) calculator now ignores
   outside clicks; an undragged one closes as before.
3. **Keycap contrast**: Pico renders `kbd` INVERTED (background = text token),
   so the app's background-only overrides left page-background text on a
   page-background chip — invisible in BOTH themes, exactly the report.
   `.help-sc-grid kbd`, `.kb-sheet kbd` and `.cmd-palette-hint kbd` now set
   `color: var(--pico-color)` (verified: light 55,60,68 on white; dark
   208,213,222 on 19,23,31).
4. **Time basis named**: SM shows run timestamps verbatim from the run
   folders — the acquisition PC's local clock — and never converts. The
   Datasets header now says so (`🕐 acquisition-PC local time` + tooltip) and
   the detail page's date row carries the same note.
5. **New-runs pill dismissal**: Escape or a click anywhere else dismisses the
   "N new runs ↑" announcement (acknowledge-only — the held rows still land
   on the next idle flush; nothing lost, just no longer announced).
6. **Run navigation at speed**: the dataset header gains ⇈/⇊ 10-step buttons
   (`dsNavRun(±10)`, clamped at the list ends — the server neighbor walk
   stays single-step) and a run-number box (`dsJumpRun`: the open run's uid
   donates the folder half; Enter opens `<folder>:<n>`, a wrong number gets
   the route's honest not-found). Verified round trip on the real archive
   (run #154).

## ⑦b Gaussian CZ macros — the customer's script as one button

**Request (confirmed as option b).** The customer's
`add_gaussian_cz_macros.py` builds `cz_gaussian_unipolar` +
`cz_gaussian_bipolar` CZGate macros for a pair from its calibrated
`cz_flattop`, plus pointer-linked operations on the moving qubit's z line and
the coupler. They wanted the workflow inside SM.

**What shipped.** `core/gaussian_cz.py` — a PURE planner transcribed from the
customer's own run of the script on their live chip (pair q19-20): the CZGate
skeleton, the quam_builder-0.4 field names (`padding_length`), the op labels
(`<macro>_pulse` / `<macro>_coupler_pulse`), the absolute pointer grammar
(`#/qubit_pairs/<pid>/macros/<m>/flux_pulse_qubit/<field>`), and the script's
own guard set as honest refusals (no cz_flattop / no moving_qubit role /
non-numeric amplitude / a QDAC-biased z with no operations — docs/119).
Control/target references resolve through the full pointer chain (the docs/118
two-hop lesson). Surface: a "+ Gaussian CZ…" button on the Pulses page →
`GET /pulse/gaussian-cz` (eligible-pair picker + padding / per-side filter-MHz
fields — the customer's own run used 200/50, not the script's single default)
→ `POST /api/pulse/gaussian-cz`: six `create_subtree`s under ONE change group
(one Review bundle, one Ctrl+Z — verified: one /undo removes all six),
existing macros 409 until an explicit Replace (deletes land in the SAME group,
so one undo restores the prior state exactly), archives refuse, mid-way
failure rolls back. Working copy only — Apply to live stays the user's press
(docs/107).

**Verified.** Structural golden vs the customer's chip: every key set, class
and pointer string byte-equal to what their script wrote — numeric values
differ only because cz_flattop was recalibrated since, `fidelity`/`extras`
are calibration-populated after creation, and the bipolar variant's COUPLER
pulse class was hand-changed to the Square class on the real chip (a physics
choice recorded in the pin; the builder follows the handed script — a
per-side class switch is an easy follow-up if wanted). docs/98-grade proof:
create on a real-chip copy for a macro-less pair → /save → `Quam.load()` in
the customer env succeeds and the macro's amplitude resolves to cz_flattop's.
Real Chrome: form lists 30 eligible pairs, creation stages a 6-change Review
bundle, console clean. **Pinned by** `tests/test_gaussian_cz.py` (12).

## Follow-up: the CQT XEB run #2560 compatibility check + bare `Pulse`

**Ask.** "This run's pulses look different from what I expected — verify it
is REALLY compatible with SM": `CQT/data/2026-08-19/#2560_38_two_qubit_xeb_192702/quam_state`.

**Verdict: fully compatible, and structurally IDENTICAL to the chip you have
been working with.** Diffed against the PJ_10082026 copy: 7,757 leaves vs
7,757, **zero** structural differences (629 pulse/gate nodes — none added,
removed, or class-changed), and only **23 numeric leaves** moved — 15 inside
`q19-20`'s macros and 8 port filter values, i.e. ordinary recalibration
between 08-14 and 08-19. Battery: all 8 pulse classes resolve (7 exact +
CosineBipolarPulse via today's ⑦a), QuamStore loads with 0 pointer warnings,
all 11 SM pages render 200 with 0 unrecognized banners / 0 error toasts, and
the definitive test — `Quam.load()` + `machine.generate_config()` in the
customer env, in memory, read-only — succeeds (90 elements / 354 pulses /
526 waveforms, the 11 QDAC trigger pulses included).

**The one thing SM said wrongly, now fixed.** The chip carries 11 bare
`quam.components.pulses.Pulse` at `z.opx_trigger_out.operations.trigger` —
the QDAC trigger markers (docs/119). quam's own `waveform_function()` returns
**None** for the bare class (verified in the customer env): it is a
digital-marker-only pulse, fully loadable, fully config-generatable. SM's
catalog had no entry, so anywhere it surfaced it wore the "Unrecognized pulse
class" scare. Now: a catalog spec (`label="Digital marker only"`, never
creatable) and a synth answer that says the truth — `digital_only: True`,
"digital marker only — this pulse has no analog waveform" — instead of
inventing a flat line or crying unrecognized. (The Pulses table deliberately
still lists waveform pulses only — 524 rows; the trigger markers are wiring
plumbing. Listing them is an easy follow-up if wanted.) Pinned by
`tests/test_gaussian_cz.py::TestBarePulseRecognized` + the catalog-shape pin.

## Round 3 — Auto-Sync polish, Versions, topology markers, chrome, honest outliers

The second feedback session on the round-1/2 work, all items discussed and
confirmed before any code. Verified end-to-end in a real browser
(`_rt_cfb2_topo2.cjs`, `_rt_cfb2_asv.cjs` — both ALL OK on the 20Q chip).

### r3-0 Auto-Sync is visible, anchored, and modern

- The pill's ⚡ was an emoji, which CSS cannot recolor — replaced by an inline
  SVG bolt (`icon_bolt` macro in `_icons.html`, currentColor). OFF = muted
  gray; ON = `#f59e0b` orange bolt with a drop-shadow glow **and** an orange
  pill border (`.auto-apply-on`, `!important` over the warning-palette base)
  so the armed state is unmistakable at a glance.
- The panel used to open at the window's LEFT edge: `#auto-sync-pop-host` is
  an unpositioned span, so the absolutely-positioned popup resolved against
  the page. `AutoSync._place()` now positions it `fixed` under the pill's own
  rect, clamped to the viewport (probe: pop left 1039 vs pill 1039).
- Restyle ("촌스럽다"): compact buttons, blur backdrop, `…and replace` drawn
  as a genuinely subordinate row — 1.9rem indent plus a 2px `::before` tree elbow, so
  the subordination is visible, not just whitespace — and full color-inversion
  on row hover (`.as-row:hover` swaps `--pico-color`/`--pico-background-color`).

### r3-1 The Versions button says a word, and answers the question itself

- The chip's label was the raw snapshot token (`20260819_114833_6782`) — "정보가
  아닌 것이 버튼의 이름". Label is now the word **Versions** + count; the exact
  id moved to the tooltip ("The live chip is on recorded version <ts>") and the
  panel rows. `unrecorded` renders only when history EXISTS and live matches
  none of it (with zero versions there is nothing to call unrecorded).
- The panel now leads with the **quick diff**: `state_versions_panel` runs
  `hm.diff_snapshots` over the two newest rows and, at ≤50 changes, renders the
  full `path | old → new` table through the one docs/76 `delta_chip` — the
  question users bring ("what just changed?") answered with zero picking. >50
  changes states the count and defers to the kept tick-two → Compare flow.
  One version → no block (a comparison against nothing would be a lie).
  Deliberately NO per-row ✕ (user retracted it: reverting values is the Review
  tray's job; a second surface doing it would blur the covenant).
- **Realtime during Auto-Sync**: while a session is armed, every flush ends in
  a tray swap (docs/117), so StateVersions rides `htmx:afterSwap` on the tray,
  debounced 900 ms, refetching `/state/versions` with ticked rows preserved.
  Real-browser bug this exposed: the listener sat on `document.body` — but
  app.js executes from `<head>` where body is null, and the throw killed the
  WHOLE IIFE (`StateVersions is not defined`; toggle dead). Moved to
  `document` (htmx events bubble there); pinned with a source assertion since
  jsdom evals app.js with a body present and can never see this class.

### r3-2 Help · Settings · Calculator

Help moved to the FIRST slot of the sidebar tools (user's order), same
`sidebar-tool` row style as its siblings.

### r3-3 Topology: the numbers beat the markers, and gates are toggleable

- 2Q IRB values were covered by the C/T/M role markers at the edge midpoint.
  Markers now half-overlap the stones (inset `CELL*0.30`, radius
  `max(3.6, CELL*0.062)`) and the metric values (`evalSvg`) are drawn LAST —
  top layer, pinned by DOM order in both the selfcheck and the browser probe.
- Per-gate (pulse-variant) toggle: when a 2Q metric has entries under more
  than one gate name, a sub-row (`.topo-hero-gatebar`) lists `best` + each
  real gate (`cz_flattop`, `cz_gaussian_bipolar`, …); `edgeBest2Q` filters the
  edge evaluation to the chosen gate, persisted under `quam_topo_hero_gate`.

### r3-4 Chrome: hamburger 3-state, sticky band, banner ✕

- Hamburger `cycleChrome()`: click 1 collapses the sidebar; click 2 hides the
  topbar too, leaving a floating ☰ (`.chrome-reveal`); clicking that restores
  everything. Probe-verified all three transitions.
- The sticky topology subnav floated over a 12.6 px see-through strip: sticky
  inside a padded scroll container pins at the padding edge while overflow
  clips at the padding box. Fix = negative `top` by the pane's own padding +
  compensating padding inside the bar. Measured after: 0.0 px.
- Every status toast carries a working ✕ (`.toast-x`); the recurring green
  "data folder" banner got concise wording and a session-scoped dismiss
  (`sessionStorage` keyed on the banner's content signature).

### r3-5 Small screens: zoom + compact

Hero map zoom is a slider driving the SVG width **in place** (never a rebuild
mid-drag), default dialed back to 1.7× (was 2×) and auto-fit-clamped;
compact mode (`hero-compact`) shrinks stones 37→33 and grows the value font to
13 px — relatively bigger numbers on a smaller map, per the ask.

### r3-6 Outlier over-calculation (user: "이건 명백한 over calculation이야")

q4's 99.67 % RB was flagged "outlier 16.8× MAD" among 99.85–99.92 % siblings:
on a tight distribution MAD collapses toward 0 and the modified z-score
explodes — a value INSIDE spec was being marked deviant for being 0.2 %
different. The rule everywhere is now **spec first**: a value the chip-health
threshold judges `pass` is never an outlier; a warn/fail value still flags on
the statistics; with no threshold for the metric, a practical floor
(|v−median| ≥ 1 % of |median|) keeps micro-deviations quiet. Applied in BOTH
implementations (`chip-status.js` `outlierScorer` + `core/report_card.py`
`_outliers`) and verified on the real chip: q4 unflagged; genuinely-below-spec
values (83 % assignment, 29 µs T2echo) still listed.

## #20 The sidebar experiment filter lagged, and sometimes froze on a stale query

Reported as: type → clear → retype in the dataset LEFT-panel search "lags or
stops responding entirely, very often". Measured on the customer's real
2,655-run archive: every keystroke request cost 200–500 ms server-side and up
to 263 KB of HTML — and the SLOWEST render is the empty query, which is
exactly what the "clear" keystroke requests. Three independent defects:

1. **htmx queue-last stacking.** The box had no `hx-sync`, and htmx 2.0.4's
   same-element default queues the newest request BEHIND the in-flight one —
   so clear-then-retype waited for the full-tree render before the filtered
   one even started (~1 s of dead box). Now `hx-sync="this:replace"`: a new
   keystroke ABORTS the in-flight request.
2. **The poller refetch raced in a different sync group.** The version-gated
   tree poll re-fetches with the current filter — from `htmx.ajax` with no
   source element, i.e. OUTSIDE the textarea's sync group. During a live run
   the workspace version bumps on most polls, so a refetch issued mid-typing
   could land AFTER the newer keystroke response and pin the tree on a stale
   query with nothing left to correct it — the "stops entirely". The refetch
   now issues **through the filter element** (`source: f` — one sync group,
   so the last-issued request is the only one that can render) and the poll
   tick defers entirely while the box is focused (a 200+ KB DOM swap must
   never land under someone mid-search; `lastV` holds, the next idle tick
   catches up).
3. **`keyup` missed mouse paste/cut** — the trigger is now `input changed
   delay:250ms`, and the filter-pill ✕ fires a synthetic `input` to match.

Server side, two memos (both keyed on the workspace's own `version`, which
every tree mutation already bumps): `_tree_render_ctx` reuses the per-root
`build_nested_tree` model (~200 ms of the 350), and `workspace_tree` caches
the whole UNFILTERED response (the template renders from pure context — no
request/session state). Measured after: clear-the-box **1 ms** (was 350–500);
filtered queries 25–237 ms proportional to matches, with abort making only
the final one visible. Filtered trees never touch either memo.

Real-browser verified on the live archive (`_rt_cfb2_sidebar_search.cjs`):
rapid type-clear-retype converges in ~1 s including the typing itself; a
stale refetch injected mid-typing cannot land last. Pinned by
`tests/test_web.py::TestSidebarSearchLagFix`.

## #21 The printable chip report (printer icon beside Chip Status)

Customer request: a small printer icon to the right of **Chip Status** in the
sidebar that generates an HTML with the qubit components and topology.

`GET /chip-status/report` is a STANDALONE page — no app chrome, forced light
theme (a report commits to one look; it is what a printer produces anyway):
header (chip name, generated-at, source folder, counts), the component-map
drawing, and read-only unpaginated tables of all five component views. Nothing
is recomputed for the report — the map is the SAME ComponentMap machinery the
component pages mount (it fetches `/api/topology`; the mount shape is pinned),
and the tables render the same QueryEngine rows with the same `qty`/`phys_amp`
formatting, including the honest text-value quoting and the degraded
unreadable-pair row. A chip with no OPX flux / no couplers states that in
words, never a bare heading.

The toolbar offers **Print** (`window.print()` + `@media print` rules) and
**Download HTML**: `ChipReport.buildStandalone()` waits for the map SVG, clones
the document, strips every `<script>` and the now-dead download button (Print
keeps its inline onclick), inlines the stylesheet, and hands back ONE
self-contained document — measured 645 KB with the drawn 20Q topology baked
in, openable anywhere. The sidebar affordance is `icon_printer` (SVG,
currentColor — the docs/89 emoji rule) in the existing `.nav-sub-row`,
`target="_blank"`. No chip open → a friendly page, not an error.

Verified in a real browser on the 20Q QDAC chip (`_rt_cfb2_report.cjs`:
160 map marks, 20 unpaginated rows, 30 pairs, standalone output pinned
script-free/inlined/SVG-baked). Pinned by `tests/test_chip_report.py`.

### r3 bug 1 — the Auto-Sync toggles looked dead

Report: pressing a switch in the popup changed nothing on screen. It DID
toggle (measured checked true→false in a real browser) — but the restyle's
`.as-row input { width: auto }`, written to dodge the global
`input{width:100%}`, collapsed the `appearance:none` checkbox to a **4px
sliver** whose checked and unchecked states were the same blue bar (the
border stays accent-colored in both). Fix: an explicit 1.1rem square, so
Pico's own `:checked` checkmark renders — checked = filled box with a check,
unchecked = empty box, measured 23×23px live. Pinned by
`test_the_panel_checkboxes_have_real_dimensions`.

### r3 bug 2 — the Versions panel rendered sideways and ran off-screen

Two causes, both measured live: the panel lives inside the topbar `<nav>`,
and Pico's `nav ul { display:flex }` reaches nested lists — the version rows
flowed HORIZONTALLY with a scrollbar (`.state-versions-list` now declares
`display: block` itself); and the CSS anchor (`left: 0`, 46rem wide) pushed
the panel 526 px past the viewport when the chip sits right of center —
`StateVersions._clampToViewport` nudges the open panel back inside (measured
right edge 2126→1592 on a 1600 px window). Plus, per the user: the restore
button label shortened to **↑ Pull to Live** — the act (overwriting the live
chip, both force gates intact) stays named in the title, and the button keeps
the one error-tinted overwrite style. Pinned by `TestPanelLayout` + the
updated act-naming pin; long quick-diff values now wrap
(`overflow-wrap: anywhere`).

### r3 제안 1+2 — M marker style, floating-wiring border

- **M matches C/T** (제안1): the M readout marker wore the accent (blue ring
  + blue letter) and shouted over the whole map; the two override rules are
  gone, so M inherits exactly the neutral C/T dot style — verified live:
  identical computed stroke/fill/text across M and C.
- **Floating Instrument Wiring border** (제안2): the muted hairline melted
  into the page. Now an accent-tinted border (55% primary mixed into the
  muted border color) + an 18%-primary 1px glow ring — a frame, not a
  highlight; theme-derived, not hardcoded.

### r3 버그 3 — the run jump belongs on the vs-prev bar (the ⑥ header copy was a misread)

The ⑥-era header jump box + ⇈⇊ duplicated a feature the request had always
meant for the EXISTING Prev State comparison bar (`‹ older #154 vs #153
newer ›`). Consolidated: the header keeps single-step ↑/↓ only, and the bar
gains **« ten hops back · ‹ older · #154 vs #[typeable run id] · newer › ·
» ten hops forward**. Ten hops are computed server-side along the same
state-carrying candidate walk older/newer already use (self-diff skipped,
clamped to the farthest reachable run — never dead when fewer than ten
remain). Typing a number compares straight against it; a number with no
saved state falls back to the default comparison AND says so
(`Run #9999 has no saved state in this folder — showing the previous run
instead.`) rather than blanking the pane. One Pico trap re-hit and pinned in
the comment: `input:not([type=checkbox],…)` carries (0,1,1) and beats a lone
class, so the run box rendered 213 px wide until the selector went two-class.
Verified on the real 154-run archive (`«` 153→143, typed 120, 9999 fallback);
pinned by the updated `TestDatasetNavR16` (+ ten-step clamp test) and
`TestRunJump` (header carries NO jump box).

### r3 스타일 3종 — dark report, whole-row active highlight

- **The report is DARK now** (user: "SM에서 경험하는 그대로"): the light-forced
  first cut washed the map out — and the REAL defect underneath was that the
  report never linked `pico.min.css`, so every `--pico-*` base token was
  undefined and the body rendered transparent. Both sheets link in base.html's
  order, the report-local styles are tokens (not hardcoded colors), the
  download inlines BOTH sheets, and `print-color-adjust: exact` keeps the dark
  ink at the printer (the browser's default background-stripping would print
  light text on white paper).
- **Active rows paint to the edge**: Instrument Wiring's ⧉ and Chip Status'
  🖨/▾ sit OUTSIDE the anchor, so the blue active pill stopped mid-row.
  `:has(> a.active)` paints the whole row (`li.nav-floatable` /
  `.nav-sub-row`), the anchor goes transparent, and the icons inherit the
  inverse color with no box of their own. Measured live: row bg
  rgb(1,114,173), icons inside, ⧉ background transparent.

### r3 심플 버그 + 제안 — sidebar duplicate activation, brand-area progress

- **Duplicate activation**: three independent active-setters fought — the
  `htmx:pushedIntoHistory` handler compared query-carrying hrefs against the
  bare first path segment (Chip Status' `?view=` subnav links never toggled,
  and the same-href Chip Components parent + Qubits child BOTH lit), while
  `chipNavView`'s manual push/replaceState fires no htmx history event at all
  (nothing ever CLEARED the previous menu — the reported Qubits+Topology,
  Trends+Pulses, Param History+Trends states). ONE canonical
  `syncSidebarNavActive()` now clears everything and re-derives the active
  set from the URL (path + `view` param; same-href twins go to the CHILD per
  base.html's own rule; Chip Status parent + its view child both light,
  matching the server's full-load render), wired into pushedIntoHistory /
  replacedInHistory / popstate / both chipNavView branches / the datasets
  fallback. Real-browser verified across the exact reported sequences.
- **NavProgress** (제안): a heavy first open (Param History on a big chip)
  gave no sign of life. After a 400 ms grace the brand area becomes a blue
  indeterminate sweep + an elapsed-seconds counter (tabular-nums, 100 ms
  tick), hiding when the last #table-pane request settles. Honest by
  construction: the server reports no progress, so no percentage is invented
  — what the user asked to see is that time IS passing, and how much.
  Terminal-event dedup via WeakSet (htmx can fire afterRequest AND
  responseError for one xhr; sendAbort under hx-sync replace), reduced-motion
  fallback = solid bar. Pinned by `TestSidebarActiveSync` + `TestNavProgress`.

### r3 follow-up — real counting on the brand indicator ("12/1000 → 24/1000 → …")

The elapsed-seconds counter stays the default, but a loop that KNOWS its
count now reports it: `core/progress.py` is the opt-in registry (context
manager; a crashed loop is age-swept so a stale counter can't outlive its
work; nothing estimates or invents a percentage), `/api/progress` serves the
newest active op, and NavProgress swaps elapsed time for `done/total`
whenever one exists — its poll runs ONLY while the indicator is visible, so
an idle app pays nothing. Two real producers wired: the **leaf-index
rebuild** (the 1.2–5.4 s parse of every snapshot — `Progress("Rebuilding
change index", total=len(merged))`) and the **workspace backfill**, whose
existing `progress_cb` now feeds the registry AND the status endpoint from
one set of numbers; the param-history poller also pushes the same counts
straight into the brand via `NavProgress.external` (that phase has no pane
request in flight, which is exactly when the user is watching). Every
terminal branch of the poller releases the counter. Verified end-to-end: a
REAL backfill showed the op in `/api/progress` while running and `{}` after
finish; the browser rendered `12/1000 → 142/1000` (external) and `371/1154`
(poll path) and cleaned up on settle. Pinned by `tests/test_progress.py`.

### r3 — the workspace Refresh button shows it is working

Pressing ↻ gave no feedback for the whole rescan (bounded at 20 s by
docs/105) — pressed-or-not was indistinguishable. htmx already stamps
`.htmx-request` on the trigger for the in-flight window, so this is CSS
only: the glyph (now a rotatable span) spins accent-colored, duplicate
clicks are ignored (`pointer-events: none`), and reduced-motion gets an
opacity pulse instead. Verified live mid-rescan: htmx-request present,
accent color, animation running, rest state restored on swap. Pinned in
`TestSidebarFeatures::test_refresh_button_shows_in_flight_state`.

**Round 2 (user re-report: "여전히 안되는데")**: the CSS-only version gated
the spin on `.htmx-request` alone — correct, and invisible: on a small
workspace the rescan settles in MILLISECONDS, so the animation got one or
two frames. The verification chip's 2,655-run root made it look fine in the
probe; the user's 69-run folder didn't. Now the PRESS arms the spin
(`.ws-kick`, app.js) for at least 700 ms — or the real request, whichever is
longer — and completion flashes a ✓ (`.ws-done`, 1.1 s). A press is always
seen regardless of rescan speed. Probe updated to click on a fast path and
assert the hold + the checkmark.

**Round 3 (user: the ✓ appears but NOTHING moves until then)**: rotating the
↻ glyph is imperceptible — the character is nearly circular, so spinning it
about its own center moves only the tiny arrowhead; and under reduced motion
the fallback was a faint pulse. The in-flight state now hides the glyph and
draws a REAL ring spinner (one lit quadrant orbiting a 13px ring) — kept
under reduced motion too, just slower, because a spinner that exists but
cannot be seen is the exact bug being fixed. CSS-only change.

**Round 4 (user: the ring still doesn't READ as moving)**: rotation is now
driven by requestAnimationFrame in JS — immune to any environment that
freezes CSS animations — and the rotating shape is **◐, a half-filled
disc**, whose sweeping fill is unmistakable motion at any size (a 13px
quadrant ring was not). The glyph and transform restore before the ✓; the
CSS ring survives only as the no-JS `.htmx-request:not(.ws-kick)` fallback.
Probe measures the angle ADVANCING between real frames (5°→106° over
200 ms).
