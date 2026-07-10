# 49 — Compare Hub (통합 비교 허브) — Design

Status: **IMPLEMENTED (v1, P0–P4 shipped on feat/compare-hub, 2026-07-04).**
P0 `a45fa6b` quick wins · P1a `1e76dbe` engine core (170 tests + real-fleet
goldens) · P1b `c17168b` hub UI + 5-lens adversarial review fixes `27046fd`
(27 confirmed findings incl. a P0 Jinja dict.keys 500 on the variant-B family;
A7 _is_htmx history-restore fixed GLOBALLY) · P2 `7c37823` structure strip +
fingerprint suggestion + State/Param-History deep links + drop-stash · P3
`628cb0e` mapping editor + A1 persistence (record key = min network_token +
sorted path anchors) + U7 guard · P4 legacy redirects (/diff, /compare,
/chip-compare → hub; HTMX gets HX-Redirect per A7) + sidebar 3→1.
Legacy tab FRAGMENTS (/compare/*, /chip-compare/topology|diff) still served
until the redirects soak — deleting them + their templates is the remaining
cleanup. Deferred-to-v1.1 list at the bottom of this doc still applies.
Original design + binding amendments below, kept verbatim.

Previous status: DESIGN — approved direction, pre-implementation. Synthesized from a
4-agent design round (scenario research / UX / comparison semantics /
architecture), all grounded in the real fleet
(`<quam-states>/*`, archive runs, 6 architectures inspected).

## Why (the user's framing, confirmed by measurement)

Three overlapping surfaces (/diff, /compare, /chip-compare) all treat
comparison as *diff* — "same thing, two versions." That works for the same
chip over time (measured: adjacent runs differ by 1–50 leaves) and collapses
for different devices (LabA vs example_lab3: 9,798-row dumps, name-collision
false-pairing — two different physical devices in the fleet share the exact
same qubit names AND grid_locations, so **device identity is provably
undetectable**). Users want everything (all differences + commonalities +
summary), just *organized* — not truncated.

## Axioms (user-decided, fixed)

1. **One Compare hub** replaces /diff + /compare + /chip-compare (sidebar 3→1).
2. **The comparison context is declared by the user** — never auto-detected
   (at most a non-binding suggestion). Three buckets:
   - ① **같은 소자, 다른 시점** (Same chip, over time)
   - ② **같은 설계, 다른 소자** (Same design, different device)
   - ③ **서로 다른 소자** (Different devices)
3. Views adapt per bucket. No minimal-info philosophy: all differences (never
   truncated), commonalities, a summary layer, a **compact** structure strip
   (gen-config board-grid style, not the full topology page), and a
   line-by-line drill-down.
4. Input is free: drag-drop any folder, workspace chips, archive-run
   quam_state, Param/State-History snapshots, working state — with honest
   labels always.

## Scenario grounding (14 real scenarios; the taxonomy holds)

Post-calibration review, drift bisection, thermal-cycle recal worklist,
backup verification (users literally keep `LabA - 복사본` folders), wafer
twins, same-design-other-lab, subset chip (example_lab3 15/21 populated, dangling
pair `qB3-qA4`), candidate selection, customer-vs-demo debugging, golden-state
schema upgrade, wiring-only re-cable, wizard-variant compare, cross-arch graph
portability, long-horizon trends (→ links out to Param History, not rebuilt).
Two things are **axes, not buckets**: scope (full / wiring-only / subtree
filter) and subset population (a bucket-② mapping feature).

## The page (one surface, three zones)

```
[A] SOURCES — the basket. Hairline rows: ordinal + ⭐ref + honest label
    (chip_name_for + snapshot ts + origin badge LIVE/WORKING/RUN/HISTORY/
    FOLDER/DROPPED) + full path on the second line when ambiguous + drag
    reorder + inline alias rename + remove. Same chip addable N times with
    different time points (this IS the bucket-① flow). Unreadable folders =
    honest error rows, excluded from count.
[B] CONTEXT — inline segmented control (①/②/③), one-line sublabels +
    a concrete example each. Suggestion line (fingerprint-based) with a ghost
    [Use ①] button — never pre-selected. Changeable mid-session; basket kept.
[C] RESULTS — bucket-adaptive tabs. After compare, A+B collapse to a one-line
    toolbar summary with [Edit sources].
```

**Source pickers**: Current chip (one click — working state; live as option),
Workspace ▾ (chip → **"which state?" popover**: Live / Working / snapshot
list [State-History row language, filterable] / archived run — default Live;
fixes the current silent-oldest-run trap), History ▾, Recent ▾, Browse
(shared folder browser). Drops accepted anywhere on the page; multi-folder
drops add each. Deep links in: State History rows, Param History drawer
checkboxes, dataset run detail, Chip Status header each gain `⇄ Compare…`
that pre-fills the basket (context still awaits the user's click).

## Per-bucket results

**① Same chip, over time** — the existing good diff promoted. Columns
time-ordered. Hairline rows (review-modal language: M/A/D gutter, dimmed
path dirs, mono values, per-cell Δ vs ⭐ref). Tabs: Changes / Summary /
Common / Line-by-line. Structure strip hidden unless wiring actually changed
(then an amber banner expands it). Schema changes on the same device (quam
upgrades) render as `schema-changed` rows — real, not bucket-③-only.

**②/③ layered page** —
```
STRUCTURE  — mini TopoGraph.renderStatic cards side-by-side (cell:22,
             stoneR:8), chip-type-aware edges (CR arrow/CZ dashed/coupler
             dot), REF border, `layout: auto` chip when grid_location absent.
             ② tints stones whose mapped counterpart differs; ③ never tints
             (no correspondence — the strip is shape-at-a-glance only).
SUMMARY    — curated rows × source columns. ②: per-qubit/per-pair values
             under the mapping (Δ vs ref, ◆ beyond tolerance). ③: cells
             become distributions (median [min–max], measured/total counts).
             Sortable; units via core/units.py; `—` for absent, never blank.
TABS       — Differences / Common / Line-by-line (shared implementation).
```

**② qubit mapping bar** (collapsed one-liner between STRUCTURE and SUMMARY):
`▸ Qubit mapping — by grid position: qA1↔q1 … 9/9 mapped [Edit]`.
Auto-map algorithm (normative): **grid_location match first** (present on
every real chip; it IS the design position; declaration order is scrambled on
real chips — ExampleChip9Q declares q0,q3,q6,… — so positional-by-index is
forbidden), fallback natural-sort name zip, else straight to manual. Method
badge always shown. Edit mode: dropdown per cell (primary) + drag-to-swap
(bonus); unmatched qubits listed dim, excluded — never zero-filled. Confirmed
mappings **persist per (fingerprint-pair)** and reload. Pair mapping is
DERIVED from the qubit map via resolved endpoints (never pair-name strings);
CR pairs are directional (both q0-4 and q4-0 exist as distinct objects) and
only match same-direction; CZ pairs may match flipped with an
`orientation flipped` annotation (control/target leaf roles cross-mapped).
Dangling endpoints (real: example_lab3 `qB3-qA4`) render as flagged orphans.

**③** hides the mapping bar; a lightweight "compare these few qubits"
cherry-pick feeds the bucket-② leaf machinery for just that subset.

## Comparison semantics (locked; full detail in the semantics spec)

- **ComparisonSnapshot** normalization: flat_raw + flat_resolved (pointers
  resolved once via the store's per-instance cache; `#./` self-refs kept raw
  as "runtime" values), ptr_kind per leaf, resolved pair endpoints, structure
  descriptor, ChipFingerprint + content_hash identity.
- **Values compare on resolved leaves.** The pointer GRAPH is compared
  separately: value-equal but link-changed (literal↔pointer flips are real —
  the downconverter fix-up) = `link-changed` class, counted as schema/link,
  not physics drift.
- **Row classes (closed enum)**: equal · negligible · modified · added /
  removed (① only) · not-in-this-design (②/③ one-sided; neutral, never red) ·
  not-in-source (missing wiring.json) · link-changed · type-changed (40 vs
  40.0 — a badge in Common, not a difference) · schema-changed (`__class__`) ·
  provenance (`*_updated_at`, `__package_versions__` — excluded from counts,
  one toggle away) · unresolved (dangling — amber, never red).
- **Tolerance: no global slider.** 3-position strictness (Exact / Normal /
  Coarse) + per-dimension advanced table (freq/time/duration_ns(int=exact)/
  volt/fidelity/phase/dimensionless), persisted in localStorage. Counts show
  both: "312 differences (41 above negligible)".
- **Wiring**: separate Infrastructure section always; ① counted (drift!),
  ② classified infrastructure (visible, excluded from headline), ③ structure
  descriptor only. `network.*` = identity aid, never diffed.
- **2Q fidelity canonicalization** (the LabB incident): follow the active-gate
  pointer (`macros.cz → cz_unipolar`), prefer nested
  `StandardRB.average_gate_fidelity`; a bare-float `StandardRB` is labeled
  **"Clifford fid."** — never visually conflated with gate fidelity.
- **Summary set**: f_01 + XY RF (divergence badge when |f_01−RF|>1kHz — real
  chips diverge intentionally), anharmonicity, resonator RF, T1/T2ramsey/
  T2echo (physicality-gated via chip_health), readout fidelity (derived from
  confusion diag), 1Q RB, x180/readout amp+length via **alias pointers**
  (lab-portable; NOT QueryEngine.get_qubit's hardcoded DragCosine), flux
  offsets (auto-dropped when all-null), pair gate inventory + fidelities +
  amps + coupler offsets + CR freqs.
- **Coalescing**: one-sided subtrees collapse to their highest absent
  ancestor ("`coupler` — absent on B, 12 leaves", expandable); Common view
  coalesces equal subtrees ("identical (14 leaves)"). "Never truncated" =
  everything reachable by expansion; nothing dropped.
- **Outliers (③/② N≥5)**: robust MAD z>3, only over physicality-passed
  values; N<5 → min/max annotations, no statistics. No verdicts ever ("chip A
  is better" stays human).

## Architecture (decisions)

- **Stateless, URL-canonical**: the basket IS the query string
  (`src=ws:…&src=hist:chip/ts&src=drop:sha12&bucket=2&tol=lab&ref=0&map=…`),
  hx-push-url → reload-safe, shareable. No Flask session (house has none).
- **Drops are stashed**: `POST /compare-hub/stash` writes dropped
  state+wiring via safe_io to `instance/compare_drops/<sha12>/` (LRU-GC cap
  ~20) → `drop:` tokens survive reload.
- **`core/compare_sources.py`**: CompareSource dataclass + **dedicated store
  pool** (own LRU 8, keyed by content_hash) — never touches the scanner LRU
  or `_quam_cache`; regression test pins both untouched after a hub render.
  `working:` origin reads the active context's in-memory dicts (compare the
  user's UNSAVED working state, not the live files — today's /chip-compare
  silently compares live).
- **`core/compare.py`** (~500 LOC): alignment (per bucket), flatten_resolved,
  path remapping under the map, tolerance engine (units.py dimensions),
  summary extraction via `_BULK_COLUMNS_SPEC`+`resolve_field_target`
  (core/pointer_path.py) + `pair_columns.derive_pair_columns`, CompareResult
  LRU keyed (sorted content_hashes, bucket, tol, map_hash).
- **`core/param_specs.py`** (P0 extraction): `_QUBIT_PROPERTY_MAP`,
  `_BULK_COLUMNS_SPEC`, `_PAIR_PROPERTY_MAP`, `_COMPARE_PROPS`,
  `_TABLE_PROP_GROUPS` move out of routes.py (re-import shim keeps names).
- **`differ.py` untouched** (Param/State History + review modal + ~35 test
  files depend on it); `multi_compare` stays only for /trend until migrated.
- **Structure strip = topo-graph.js** (already the standalone read-only
  renderer; +~40 lines: tolerant fallback layout + per-node class hook —
  jsdom-pin the Populate mirror against regressions). NOT wiring-grid.js
  (editable, wizard-coupled).
- **Rendering budget** (measured: load 20ms/chip, diff 9ms — DOM is the only
  bottleneck): ≤~1,500 rendered rows per HTMX partial; groups collapsed with
  lazy per-group endpoints; "expand all" switches to `compare-virtual.js`
  (~150 lines of window math borrowed from dataset-virtual.js — the pattern,
  not the 1,817-line file).
- **Legacy**: /diff + /chip-compare → 302 into the hub with mapped params;
  /compare's sidebar-checkbox POST becomes a deep-link adapter (kept — good
  UX); templates deleted + tests rewritten only after redirects soak.

## Anti-requirements (explicit)

No editing from compare (deep-link to Explorer/Live Edit instead; no
"copy A→B" in v1). No bucket auto-commit. No topology-board duplication
(static minis only). No trend rebuild (link to Param History). No
working-vs-live review duplication (review modal owns it), no state-vs-config
verify (Pulses owns it). Config-output comparison = v2 (spec locked: element
names templated under the qubit map; waveforms by content hash + max-abs-dev,
never element rows; v1 ships only a "config impact" teaser when fresh cached
previews exist). No raw 11k dump as any default view. No forced pairing /
zero-fill. No global device-identity registry (mappings persist per
fingerprint-pair only). No statistical verdicts.

## Phases (each shippable)

- **P0 (~1d)**: quick wins on existing surfaces — honest labels
  (chip_name_for+ts), `_detect_workspace_chips` newest-run fix, param_specs
  extraction, tolerance on /chip-compare diff tab.
- **P1 (3–4d)**: hub shell + bucket ① end-to-end (sources pool, identity
  alignment, strictness presets, summary, differences w/ soft cap), sidebar
  entry added (legacy kept).
- **P2 (2–3d)**: structure strip, Common view, fingerprint suggestion,
  drop-stash.
- **P3 (3–4d)**: buckets ②/③ — grid auto-map + mapping editor + persistence,
  distributions, virtualized expand-all, line-by-line drill-down with pointer
  provenance + jump-to-Explorer.
- **P4 (1–2d)**: redirects, sidebar 3→1, template/test cleanup, docs.

Total ≈ 10–14 working days. Test strategy: real-fleet goldens (LabA vs
LabA-복사본 = 49-entry near-twin; example_lab3 vs variant-B = 9,798-entry scale
golden; both path-gated auto-skip), synthetic fab-twins in tmp_path,
per-dimension tolerance tables, pool-isolation regression, jsdom for mapping
editor + virtual window + renderStatic extensions.

## Known risks

Basket+context adds a step vs old one-shot /diff (mitigate: Current-chip
one-click, deep links, "Restore last comparison"); wrong-bucket-② misuse
silently grid-aligns unrelated chips (the contradiction warn-line is the
guard); drag-remap discoverability (dropdown is primary); virtual-scroller
regression class (`contain:strict` zero-height — browser-verify both themes);
routes.py is hot across parallel sessions (land P0's extraction first).

---

# Review amendments (BINDING — override the base text where they conflict)

Two-stage review: hostile staff-engineer audit (all findings empirically
verified against the fleet) + user-advocate walkthrough (3 personas × 14
scenarios, click-counted). Verdicts: "build with amendments — do not build
P3 as written" / "approve with F1–F4 as conditions".

## Corrections to normative specs (adversarial findings)

**A1. Mapping persistence key (was: fingerprint-pair — BROKEN).** Measured:
LabA, LabA-복사본, LabA_CR, example_lab4, example_lab4_ori and 745 archive runs ALL
share fingerprint token `b19325a76fde0a0a` (identical network block +
name-sets) → cross-contamination; meanwhile name-sets are IN the key, so a
chip growing one qubit orphans its mappings. New key:
`(network_token, source_anchor_A, source_anchor_B)` where network_token
hashes only `fp.network` and source_anchor = workspace chip key / folder
label / user alias. The qubit name-set is stored INSIDE the record and
validated on load (stale names dim, never lost). Dropped sources: mapping
session-only. N-way: persist against the lexicographically-smallest source
anchor, derive vs-ref at render (moving ⭐ must not orphan maps).

**A2. grid auto-map branch conditions (was: containment/ratio — produces
confident WRONG maps).** Measured: variant-B's 17 grid positions ⊂ LabA's 21
100% with CROSSED names (variant-B qA1 ↔ LabA qD1 while LabA also has qA1);
CR_state overlaps LabA 7/8; every fleet layout is dihedral-self-symmetric
(mirror re-declarations pass exact-match); ExampleChip9Q's grid is a degenerate
auto-assigned 1×9 line. New rule — auto-CONFIRM only when: (1) the smaller
grid set is 100% contained (no ratio thresholds), AND (2) the induced
pairing is name-consistent wherever a name exists on both chips (also
catches mirrors). Degenerate/collinear grids → distrust grid, use names.
Fallback = exact-name INTERSECTION (never positional zip — sorted-zip pairs
example_lab3 qB1 with LabA qA1). Everything else = suggested-with-warning,
explicit confirm required.

**A3. Flipped-CZ pair matching (was: cross-map control/target leaves —
wrong physics).** Per-leaf flip policy table replaces the one-liner:
swap (`phase_shift_control/target`); transform (pair 4×4 `confusion`:
permute rows AND cols by P(01↔10) — measured ~0.03 phantom diagonal drift
otherwise; `mutual_flux_bias` element swap; `moving_qubit` relabel);
exclude-with-annotation (`id` leaves — they embed qubit names in values,
`detuning` — sign convention unknowable, freeform `extras`,
`flux_pulse_qubit.*` unless post-flip moving roles agree). CR pairs:
flip NEVER offered — enforced structurally on `cross_resonance`/CRGate
presence (verified: all CR edges exist in both directions as distinct
objects).

**A4. `working:` origin + caching (was: hash the live dicts — torn reads).**
Under `store._lock`: content_hash + deepcopy(state)+deepcopy(wiring)
atomically (measured 7 ms on the largest chip); the pool holds only
immutable copies. Lock-ordering constraint: pool-miss population must never
take the build lock while holding the pool lock. Result caching: cache
per-source NORMALIZED SNAPSHOTS keyed by content_hash; assemble columns /
ref / Δ at render from the ordered source list (ref and column order must
never be inside a cache key — and dedup must not collapse a source added
twice).

**A5. Rendering budget (was: coalescing → "dozens of rows" — off by 10-100×).**
Measured post-coalescing rows: example_lab3 vs variant-B 1,451; LabA vs example_lab3 1,503;
LabA vs example_lab4 2,044 — the ≤1,500 single-partial budget breaks on real
pairs. v1: collapsed lazy groups + server-side "load more groups"
pagination; virtualized default view in v1.1 (compare-virtual.js). Giant
collapsed rows (worst: 1,661 leaves under one pair) get leaf-count badges +
one-level sub-summaries. Common-view coalescing works only for ①/twins
(measured: bucket-③ degenerates to 1,251 singleton groups) — ③'s Common IS
the summary table.

**A6. Pointer semantics refinements.** `#./` self-refs: new row class
`derived` (NOT equal) — measured: `intermediate_frequency` self-refs compare
equal-as-string while runtime values differ 183 kHz; the row links to the
source leaves where the difference surfaces. Containers (160–252 pointers
per chip resolve to whole dicts): compare by re-flattening under the
resolved subtree. `#/` chains terminating on a `#./` (4 on LabA) follow the
same `derived` rule. Missing wiring.json: classify `not-in-source` by
ptr_kind + resolution-failure flag, never by value inequality (naive
resolved-compare manufactures 92 bogus modified rows on LabA). Bulk
dangling optional-default pointers (variant-B: 60 × `#../x90_DragCosine/
detuning`) must coalesce, not amber-spam.

**A7. URL/state mechanics.** `ws:` tokens are machine-local (paths embed
WSL/Windows forms) — shareable URLs scoped machine-local; cross-machine =
chip-key+content-hash tokens later. HTMX history-restore sends
`HX-History-Restore-Request` (not `HX-Request`) — `_is_htmx()` must handle
it before the hub makes back/forward load-bearing. Legacy /diff /compare
/chip-compare are POSTs: in-handler param translation + `HX-Redirect`, not
plain 302. Pair endpoints resolve via the RECURSIVE resolver (real: variant-B
endpoints are 2-hop pointers through a malformed wiring key `qA1-A2`;
ExampleChip pair-name order contradicts roles in 6/12 pairs; example_lab3 has TWO
dangling pairs).

**A8. P0 extraction scope.** The shim must cover 7 symbols (add
`_ALL_QUBIT_PROPS`, `_ALL_TABLE_PROPS` — imported by tests/test_web.py) and
the divergence badge reuses `_FREQ_TWIN_RULES` (routes.py:2161) rather than
duplicating the |f_01−RF| rule.

## UX conditions (user-advocate findings — accepted)

**U1. The daily scenario must not get slower (the review's only regression).**
(a) State History's in-row [Diff] and Param History's drawer compare STAY,
verbatim — new anti-requirement: the hub never absorbs same-chip in-place
diffs; `⇄ Compare…` is additive. (b) For fingerprint-proven deep links
(State/Param History surfaces), the suggestion renders as a focused primary
CTA — `[Compare as ① Same chip, over time ⏎]` — one Enter to results.
Still user-declared; never for drops or manual baskets.

**U2. Bucket-② trust: visible mapping headers.** Differences/Summary rows
group by qubit with the header `qA3 ↔ q7 · grid (1,2) · mapped by grid`;
rows beneath in relative paths. Mapping never lives only in tooltips.

**U3. Four visual classes, not twelve.** Users learn: **Changed** (=
beyond tolerance; the separate ◆ dies) / **Only in <alias>** (all
one-sided classes; neutral, never red) / **Attention** (unresolved,
orphans, mapping contradictions; amber) / **Equal·within tolerance** (dim).
link-changed / schema-changed / provenance render as one muted "meta" affix
chip behind a toolbar toggle (off by default), counted separately
("41 changed · +7 link/schema").

**U4. Bucket selection IS the trigger** (no extra Compare click once ≥2
sources); the ①②③ switcher stays visible in the collapsed toolbar
(wrong-bucket recovery = 1 click, basket untouched).

**U5. Headline order**: "41 changed · 271 within tolerance · 9,486 equal"
(lead with what moved). Badge/tint = the above-tolerance count at the
active strictness.

**U6. Identical hero state**: content-hash match renders "✓ Identical —
9,798 leaves equal" as a hero line (backup-verification is binary).

**U7. Wrong-② guard with teeth**: <70% grid-matched or >50% of summary rows
beyond Wide → amber banner "These devices don't look like the same design —
3/9 matched. Compare as ③ instead? [Switch]".

**U8. Copy**: sidebar = **Compare** (hub is docs-speak); strictness =
**Exact / Lab default / Wide** with the actual thresholds in the tooltip
("freq ±100 Hz · time ±10 ns · volt ±1 µV"); "within tolerance" (never
"negligible"); "Only in <alias>" (never "not-in-this-design");
link-changed = "same value, reference rewired"; origin badge FILE (not
DROPPED). Bucket example lines: ① "어젯밤 캘리브레이션 전후 · 백업 폴더 검증"
② "웨이퍼 쌍둥이 · 같은 설계 다른 랩 · 위저드 변형 A/B" ③ "고객 칩 vs 데모 칩".

**U9. PI view (with ③ distributions, v1.1)**: direction-aware best-per-row
emphasis (a per-metric fact, not a verdict) + a self-describing caption line
(aliases, timestamps, qubit counts, tolerance preset) so screenshots stand
alone.

## Revised v1 scope (minimum lovable — ~7–9 days)

**v1 ships**: P0 quick wins · basket + drag-drop + Current-chip + "which
state?" popover + honest labels · bucket ① end-to-end + 3 strictness
presets + identical hero · bucket ② with A2 auto-map + dropdown mapping
editor + A1 persistence + U2 headers · bucket ③ as thinned ② (columns for
N≤3, min/max notes for N>3; no statistics) · Summary + Differences (lazy
groups + load-more) · structure strip · suggestion-as-primary-CTA · deep
links from State/Param History · legacy redirects.

**Deferred to v1.1**: Common tab (counts line covers the need) · ③
median/MAD distributions + physicality gating + U9 · cherry-pick subset ·
per-dimension tolerance editing UI (preset values exist internally) ·
expand-all virtualization (compare-virtual.js) · drop-stash reload
persistence (v1: dragged sources session-only, honest note) · drag-to-swap
in the mapping editor · dataset-run + Chip-Status deep links · config-impact
teaser (v2).
