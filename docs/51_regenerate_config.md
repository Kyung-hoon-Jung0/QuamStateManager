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
