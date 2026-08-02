# 51 · Re-generate Config

Re-generate lets a user take a chip they already built, **edit its structure**
(ports, bands, add/remove qubits or pairs, gate family) in the Generate wizard,
rebuild it fresh, and **keep every calibrated value** — plus walk away with a
single editable **Python build recipe** that reproduces the chip as code.

Sidebar: **Generate Config → Re-generate config** (`/regenerate`).

## Why

The Generate wizard only ever produced a *fresh* chip (defaults + design-time
seeds). A calibrated chip that needed a wiring change (moved port, new band,
added qubit) had no path forward but hand-editing `state.json`/`wiring.json`.
Re-generate closes that: rebuild the structure through the real `build_quam`,
then merge the old calibration back on. And because customers also want to
*own* the config as ordinary Python (the QM `generate_*`/`populate_*` idiom),
the flow emits that script too.

## Pipeline

```
old state+wiring
  → reconstruct_spec()          (core/regen_spec.py)   structure + populate, inferred
  → [user edits in the wizard]  (regenerate.html + generate.js, mode="regenerate")
  → run_generator(build)        (core/config_generator.py → generator/run_build.py, subprocess)
        fresh structure in a NEW folder (never the source)
  → merge_states()              (core/regen_merge.py)  carry values, graft user subtrees
  → emit_build_script()         (core/regen_script.py) single editable Python recipe
  → out_dir/{state.json, wiring.json, build_<chip>.py}
```

Orchestrated by `core/regenerate.py` (`reconstruct_from_folder`,
`run_regenerate`). The State Manager process never imports `quam`/`quam_builder`;
the build step shells out to the user-selected env, everything else is pure
JSON/string work.

### 1 · Reconstruct (`regen_spec.py`)

Inverts a chip back into the wizard's spec:

- **Wiring is pinned** — every channel's `#/ports/…con/slot/port` pointer becomes
  a hard `mw_fem`/`lf_fem` constraint, so an untouched chip rebuilds to the same
  ports; only lines the user edits re-allocate.
- **Instruments** inferred from the ports in use (MW-FEM ← `mw_*`, LF-FEM ← `analog_*`).
- **Pairs** read from `state.qubit_pairs` (authoritative — a fixed-coupler / CR
  chip has **no coupler wiring**, so reading pairs off wiring misses them). The
  coupler line is pinned from wiring only when present (tunable couplers).
- **`pair_gate`** = the dominant gate family; per-pair variety is preserved by the
  merge graft, not the single-valued spec. `mixed_gates` flags the multi-family case.
- **Populate** fully extracted (RF · anharmonicity · LO · full-scale-power · grid,
  readout, flux, 1Q pulses, per-pair CZ variant/dur/amp) by inverting
  `apply_populate`, following pointer **chains** (a channel's `opx_output` is
  `#/wiring/… → #/ports/… → port`). This pre-fills the re-opened wizard.

Fixed-coupler fact: `coupler` appears in state **only** on tunable-coupler chips;
`coupler=None` ⇒ `cz_fixed`, a coupler dict ⇒ `cz_tunable`.

### 2 · Merge (`regen_merge.py`)

Two tiers over plain dicts:

- **tier 1 — carry**: where a leaf PATH survives in NEW, the OLD scalar VALUE wins
  (the calibration). NEW keeps structure + every JSON pointer (the fresh wiring),
  so the user's structural edits hold. A NEW/OLD **pointer** always keeps NEW.
- **tier 2 — graft**: OLD-only subtrees (user-added pulse ops, extra gate macros
  the single `pair_gate` didn't recreate) are copied wholesale, then their
  absolute pointers are validated against the merged tree (`dangling_grafts`).

Guards:
- **Entity collections** (`qubits`, `qubit_pairs`, `ports`, `octaves`, `mixers`)
  are NOT resurrected — an OLD-only entity there was intentionally removed in the
  rebuild (falls to `residual_lost`). Graft still applies *within* a surviving
  entity. **`twpas` is deliberately excluded** from this guard: `quam_builder`
  can't build TWPAs, so every rebuild emits an empty `twpas` dict — a missing
  TWPA is a builder gap, never a user removal, so the OLD TWPAs are grafted back
  wholesale (residual loss 0; without this, LabA lost 156 TWPA leaves). Their
  wiring + ports are then carried too (see *TWPA carry* below).
- **Pair-id reconciliation**: the builder may name a pair `qA2-A1` where the
  source has `qA2-qA1`; both reference the same qubits, so we align on
  `(control, target)` membership and adopt the source id (nothing references a
  pair by id — verified), else every pair's calibration orphans.
- **Cross-generation schema gate**: an OLD-only key inside a `__class__`-tagged
  dict is grafted only if the NEW build env's dataclass for that class knows the
  field — otherwise it's a field an older stack generation renamed/removed and
  grafting it makes `Quam.load()` raise `AttributeError('Unexpected attribute')`.
  Dropped paths land in `schema_dropped` (never silent). See the 2026-08-02
  amendment below.

Transparency counters are surfaced in the build result:

| counter          | meaning                                                           |
|------------------|-------------------------------------------------------------------|
| `carried`        | OLD calibrated scalars kept                                       |
| `grafted`        | OLD-only leaves copied in                                         |
| `superseded`     | OLD inline value **preserved via a NEW pointer** (not lost) — e.g. a CZ pulse the old builder stored inline, the current builder references from the qubit z line |
| `residual_lost`  | OLD scalars with **no home** in the rebuild (truly not carried)   |
| `pruned_ops`     | redundant OLD operations the rebuild re-expressed, removed as cleanup (see below) |
| `dangling_grafts`| grafted subtree whose absolute pointer still doesn't resolve *after* prune + TWPA carry |
| `twpa_wiring_carried` | TWPAs whose wiring + ports were carried from OLD (see below)  |
| `schema_dropped` | OLD-only fields the NEW env's class schema doesn't know — cross-generation renames/removals, dropped instead of grafted (would kill `Quam.load()`) |

**Prune of redundant superseded ops.** A rebuilt chip can carry an OLD-form pulse
op (e.g. `…z.operations.cz_unipolar_pulse_qA1`) that the fresh build re-expressed
under a new name (`cz_unipolar_flux_pulse_qA2_qA1`) — the old copy is an
unreferenced orphan whose internal pointers dangle. Such an op is removed iff it
lives under an `operations` dict, **every** absolute pointer in it is broken, and
**nothing** in the merged tree references it (provably safe). On real LabA this
prunes 31 orphaned CZ ops; ops that are still referenced are always kept.

**TWPAs are built natively.** Modern `quam_builder` exposes
`Connectivity.add_twpa_lines(twpas, pump_constraints, isolation_constraints)` and
`build_quam` materialises the TWPAs (pump + pump_ + spectroscopy + gain/SNR
fields). So `reconstruct_spec` **pins each TWPA pump line** from `wiring.twpas`
(`{element: <tid>, line: "twpa_pump", channel: mw_fem}`), `run_build` calls
`add_twpa_lines` for them, and the emitted recipe emits the same — the rebuilt
chip has real TWPAs and the merge carries the OLD pump calibration onto them via
tier-1 (LabA: 4 TWPAs, `carried` 2719→2915, 71 config elements, residual/dangling 0).

*Fallback for pre-TWPA builders.* Only `quam_builder` 0.2.0 lacked TWPA wiring
(`WiringLineType.TWPA_PUMP`'s `"p"` collided with `PLUNGER_GATE`); there
`build_connectivity` skips the TWPA lines with a warning and `graft_twpa_wiring`
(called by `run_regenerate` after the state merge) instead carries the OLD
`wiring.twpas` + referenced ports into the rebuilt wiring/state (filling only
ABSENT keys) so the chip still compiles. On a modern builder `twpa_wiring_carried`
is 0 because nothing needs grafting.

`superseded` vs `residual_lost` is decided by walking the merged tree: a pointer
ancestor ⇒ the value lives at the pointer target (superseded); a missing key ⇒
truly lost. This is why a *pure round-trip* shows a large "via reference" count
(representation changed) but almost nothing truly lost.

### 3 · Build recipe (`regen_script.py`)

`emit_build_script(spec, chip_name)` returns the source of one standalone
`build_<chip>.py` — QM's `generate_*` (wiring) and `populate_*` (seeds) steps
collapsed into a **single editable file** (the user's explicit ask). It uses only
the public idiom (`qualang_tools.wirer` + `quam_builder` + `from quam_config
import Quam` + the template's `pair_gates`), so it drops into a calibration repo's
`quam_config/` folder and runs:

```
python build_<chip>.py [STATE_DIR]
```

- Ports are **pinned** (each line carries its `mw_fem_spec`/`lf_fem_spec`); edit a
  constraint to move a port.
- Populate is emitted as editable `QUBIT`/`RESONATOR`/`FLUX`/`PULSES`/`PAIRS`
  data blocks keyed by qubit/pair id.
- It's a **recipe, not a snapshot**: measured calibration (T1/T2, fitted gate
  amplitudes, extra pulse variants) is **not** emitted — that lives in the merged
  `state.json`. The header says so.

Emitting is best-effort inside `run_regenerate` (a script hiccup never fails the
build+merge; the filename or `script_error` is returned in the outcome and shown
in the result panel).

Verified end-to-end in the LabB env against the real 21-qubit LabA chip
(fixed coupler): reconstruct → emit → **execute** rebuilds 21 qubits + 31 CZ
pairs and `machine.generate_config()` → 63 elements.

### Exact-spec sidecar

A successful rebuild writes the exact spec to `<out>/.regen/generate_spec.json`
(keyed by the output chip's content hash). A later re-generate *from* that folder
prefers the sidecar over best-effort reconstruction (`ReconstructedSpec.exact =
True`); populate is re-extracted from the current state so displayed seeds stay
live, and a hash mismatch (chip edited out-of-band) silently falls back to
reconstruction. The sidecar lives in a **subfolder** on purpose — `Quam.load()`
reads every *top-level* `.json` in a chip folder, so a spec `.json` at the top
would corrupt the load; a subfolder (and the top-level `build_<chip>.py`, being
`.py`) are invisible to it. Both were verified to load cleanly.

## Known limits

- **Band is read from the source chip**, never hardcoded — the emitted script
  seeds each `opx_output.band` from the real port (`get_band(LO)` only as a
  fallback when a port carries no band).
- **Tunable-coupler pairs**: the emitted script branches at runtime on whether
  the rebuild already created the pairs (tunable → pairs exist from coupler
  wiring: park each coupler off + `add_gates(..., coupler=pair.coupler)`; fixed /
  CR → `add_pair_gate(c, t, ...)` creates them). Verified end-to-end for both a
  fixed-coupler chip (LabA: 21 qubits, 31 pairs) and a tunable-coupler chip
  (gen_2x3: 6 qubits, 7 pairs, couplers) in the LabB env.
- **TWPAs** build natively on a modern `quam_builder` (`add_twpa_lines`) — the
  rebuild, the merge, and the emitted recipe all produce real TWPAs (verified:
  LabA rebuilds 4 TWPAs, `generate_config()` → 71 elements). Only the legacy
  0.2.0 builder can't; there they're carried by graft instead (see *TWPA carry*).

## Tests

`tests/test_regen_spec.py`, `tests/test_regen_merge.py`,
`tests/test_regen_script.py`, `tests/test_regenerate.py` — 33 tests: 2-tier merge
rules, pair-id reconciliation, superseded/lost classification, redundant-op prune
(referenced ops kept), TWPA state+wiring+ports carry, band extraction, tunable vs
fixed pairs branch, emitter validity + real-chip round-trip, exact-spec sidecar
(written, preferred, hash-invalidated), orchestration guards (output ≠ source).
Real-data cases auto-skip when the chip folders are absent.

---

## Amendment (2026-08-01, r13): dangling pairs dropped at reconstruct + sidecar reachability

Real chips carry **dangling pairs** — a cut-down layout keeps `qubit_pairs`
entries whose member qubit was removed (deviceC ships `qB3-qA4` / `qD4-qA3`
against 15 real qubits). `reconstruct_spec` used to tail-split the pointer and
emit the phantom name into `spec.qubit_pairs` while `spec.qubits` (sourced from
wiring) held only the real ids — the wizard's step-4 gate then blocked with "A
pair references a qubit that no longer exists (qB3–qA4)" and every forward rail
click bounced back to step 4, with no way through.

- **Membership gate** in the pair inversion (`regen_spec.py`): the qubit-name
  set (same wiring-first/state-fallback source as `spec.qubits`) is built
  before the pair loop; a pair with a member outside it is **dropped with a
  visible note** (`pair 'qB3-qA4' dropped — references qubit(s) not on this
  chip: qA4`), matching the compare engine's `pair_orphans` treatment of the
  same data. Its `populate.pairs` overrides are pruned too (run_build would
  only warn-and-ignore them under a phantom key). The wiring-only recovery
  path gets the same gate.
- **Wizard honesty**: a pair `<select>` whose model value matches no current
  qubit used to silently render the "—" placeholder while the model kept the
  phantom. `qubitOptions` now emits an explicit `<name> (missing)` selected
  option and the select tints red (`.gen-pair-missing`) — the in-wizard
  deletion flow shows exactly what the step-4 message names.
- **Sidecar reachability**: the reconstruct route reads the WORKING COPY, but
  the `.regen/generate_spec.json` sidecar only ever lands in the chip's real
  folder — so it was unreachable for any loaded chip. `reconstruct_from_folder`
  gained `sidecar_dirs` (the route passes the live folder), hash-gated on the
  content actually read, so a diverged working copy never picks up a stale
  sidecar.

Pinned by `test_regen_spec.py` (drop + note + populate prune + wiring-only +
JS pin) and `test_regenerate.py::test_sidecar_found_via_fallback_dir`.
Verified against the real 15-qubit chip: 21 → 19 pairs, 2 notes,
`validate_spec == []`.

## Amendment (2026-08-02): cross-generation schema gate

**Incident.** A 17Q chip built by an old fork stack (quam 0.5.0a3 /
quam_builder 0.2.0) was re-generated in a modern qop37 env (quam 0.6.0 /
quam_builder 0.4.0). The rebuild itself was correct, but tier-2 grafted the old
generation's **schema fields** back into the fresh state as if they were user
data: `CZGate.duration_control` ×80 + `CZGate.moving_qubit` ×80 (0.4.0 renamed
the field `duration_qubit` and moved `moving_qubit` onto the qubit pair) and
`_FlatTopGaussianPulse.sigma` ×16 (removed in quam 0.6.0). All 176 values were
old-schema *defaults* (`null` / `"control"` / `2.0`) — zero calibration content
— and every one was a `Quam.load()` killer (quam raises
`AttributeError('Unexpected attribute')` on any field a class doesn't know).
The graft fingerprint: the poisoned keys sat *after* `__class__` in each dict —
quam never serializes past `__class__`.

**Fix — schema harvest at build time.** `run_build._collect_class_schemas`
(runs INSIDE the selected env, right after the build artefacts land) walks the
fresh state+wiring for `__class__` strings and dumps
`{class_path: sorted(dataclasses.fields)}` into `_result.json["class_schemas"]`
— keyed to the exact interpreter that built the state, so there is no cache to
go stale (unlike the SM-side `state_schema_cache`, which served a different
env's harvest in the incident). `run_regenerate` passes the map to
`merge_states(..., class_schemas=...)`; the tier-2 loop then refuses an
OLD-only key inside a `__class__`-tagged dict when the class is known and the
field is not, recording the path in `stats.schema_dropped` (own counter — NOT
`residual_lost`, these are deliberate drops of schema noise, not lost
calibration).

Key semantics:

- The gate's discriminator is the **immediate parent dict**: `__class__`-tagged
  ⇒ keys are dataclass attributes (schema-checked); plain container dicts
  (`operations` / `macros` / `extras`) ⇒ user namespace, grafts stay
  unconditional. A user-added op (its own `__class__` subtree under
  `operations`) therefore still grafts wholesale.
- Fallbacks are always conservative: no `class_schemas` (old `_result.json`,
  harvest failure) or a class absent from the map (env couldn't import it) ⇒
  legacy unconditional graft, bit for bit.
- A field **present** in the schema still grafts — a same-generation regen
  where the fresh build simply omitted an optional field keeps carrying it.
- `__package_versions__` (quam ≥0.6 serialization stamp) is now handled like
  `__class__`: always the NEW build's, never tier-1-carried from OLD (a carried
  stamp lied about which stack wrote the state) and never grafted or counted.

UI: the build-result merge panel shows an amber `N cross-gen dropped` chip and
lists the dropped paths in the expandable detail. Pinned by the schema-gate
tests in `tests/test_regen_merge.py` + the pipeline/harvest tests in
`tests/test_regenerate.py`; verified end-to-end against the real incident chip
+ real qop37-env schemas (gate drops exactly the 176 keys; merged == cleaned
state; schema-less merge reproduces the poisoning).

## Amendment (2026-08-02 ②): pair-membership populate reconciliation

Found by the qua-libs compatibility audit (3×3 tunable-coupler chip): the
source chip named its pairs ascending (``"q0-1"``) while the pair's actual
control was ``q1`` — reconstruct emitted ``qubit_pairs`` in the real
(control, target) orientation but keyed ``populate.pairs`` by the OLD NAME, so
run_build's exact-id seed lookup missed every orientation-flipped pair (7/12):
their per-pair CZ seeds fell back to default-family seeding with only a
warning, and the wizard's ``reconcilePopulatePairs`` outright DELETED such
keys as stale. Three-layer fix, mirroring the merge's membership doctrine:

- **reconstruct** (``regen_spec._populate_pair_key``): per-pair populate
  buckets are keyed by the wizard-canonical ``f"{control}-{target}"`` id
  (state refs first, coupler-channel wiring refs as fallback, raw name last).
- **build** (``run_build._match_populate_pairs``): the seed lookup resolves
  populate keys onto built pair ids in three tiers — exact, ``_quam_pair_id``
  spelling, qubit MEMBERSHIP — so old drafts/sidecars keep working regardless
  of spelling or orientation; only keys matching no pair at all warn.
- **wizard** (``reconcilePopulatePairs``): a non-canonical key is re-keyed by
  membership onto the canonical id instead of deleted.

Emitted-bundle machinery gained ``_match_populate_pairs`` (golden regenerated).
Pinned by ``TestMatchPopulatePairs`` + the control-target keying tests in
``tests/test_regen_spec.py``; verified end-to-end on the audit chip — the
previously-warning spec now builds with zero warnings and uniform seeding.

## Amendment (2026-08-03, r16 — docs/72): adaptive reconstruction + populate-protect

Two structural fixes moved into `docs/72_regen_adaptive_fidelity.md` (the
reference): ① `reconstruct_spec` unions the chip's declared **ports
inventory** into the FEM set (a slot whose only channel was trimmed/nulled
no longer vanishes — the SNU-17Q slot-7 report), derives qubits as
wiring ∪ state, tolerates explicit-null channels, and assembles controllers
AFTER the wiring-only-pairs recovery; ② the value merge accepts
`protect_paths` — the expanded dot-paths of the user's in-wizard Populate
edits (`core/regen_populate.py`, baseline shipped from hydration) — so a
populate edit is no longer silently reverted by tier-1. New transparency
counters: `populate_protected`, `populate_conflicts`. `/regenerate/build`
also honors `scripts_dir` now (the bundle used to hardcode
`<out>/build_scripts/`).
