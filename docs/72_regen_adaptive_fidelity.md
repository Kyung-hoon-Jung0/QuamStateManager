# 72 — Re-generate adaptivity + populate fidelity (r16 feedback ⓪/⓪-3/⓪-4/⓪-5, 2026-08-03)

Branch `fix/regen-adaptive`. Driven by the SNU-17Q SUPER-CRITICAL report:
"load the chip, click Re-generate — slot 7 is missing", plus "populate values
I edit in the wizard come back with the OLD values after the build". Users
hand-trim state/wiring freely; SM must reconstruct adaptively in ALL cases.

## §1 — Adaptive reconstruction (the slot-7 fix)

The real chip (13 qubits, q12/13/14/16 hand-deleted; pairs `q1-2`/`q17-15`;
`ports` living in STATE.json; wiring.json = `wiring.qubits` + `network` only;
slot 7 used by exactly ONE channel, q17.z) exposed the structural gap:
`reconstruct_spec` derived the FEM inventory ONLY from live channel pointers
(`note_fem` in the channel scans) and **never read `state["ports"]`** — so any
edit that removed/nulled a slot's only channel silently deleted the whole FEM
from the wizard (and, through the wiring-first `known_qubits` gate, the
qubit's pairs).

Fixes in `core/regen_spec.py`:

- **Ports-inventory union**: after every channel scan, the declared
  `ports` inventory (from state.json OR wiring.json; `mw_outputs`/`mw_inputs`
  → MW, `analog_outputs`/`analog_inputs` → LF; slot keys are STRINGS) unions
  into `fems`. Channel evidence wins slot-type conflicts; inventory-only
  slots get an honest note ("slot con1/7 (LF-FEM) has no channel pointer —
  kept from the ports inventory"). Clean chips emit ZERO new notes.
- **Controllers assembled LAST** (just before the spec dict): the
  wiring-only-pairs recovery loop ran AFTER the old early assembly and its
  `note_fem` additions were silently lost — a latent second slot-dropper.
- **Qubit inventory = wiring ∪ state** (wiring order first): a state qubit
  missing from wiring is kept (note: "ports will be auto-allocated"), so its
  pairs survive the membership gate; a wiring-only qubit is noted too.
  Notes only fire when wiring.qubits is non-empty (no noise on wiring-less
  loads).
- **Null-channel tolerance**: real chips serialize channels as explicit
  `null` (Explorer nulling / hand edits). `ch.get("rr", {})` returns None
  for present-but-null keys — every channel read now uses the `or {}` idiom
  (`rr`/`xy`/`z`/pair `c`), and `state["twpas"]` as a LIST (real chips ship
  `[]`) is guarded. `/regenerate/reconstruct` catches broad `Exception` into
  an honest JSON error instead of a blank wizard on a raw 500.

Pinned by `tests/test_regen_spec.py` (customer-shaped `_snu_shaped` fixture:
ports-union, state-only qubit + pair survival, null channels, twpas-list,
zero-noise-on-clean, wiring-only-pair FEM ordering).

## §2 — Populate-protect (wizard edits survive the merge)

`regen_merge.merge_states` tier-1 carries every OLD non-pointer leaf that
exists in both trees — including the paths `apply_populate` just wrote from
the user's edited Populate values. Every wizard edit was silently reverted.

Design (adversarially reviewed; docs/72 is the reference):

- **Baseline from hydration**: `hydrateFromSpec` deep-copies the displayed
  `spec.populate` into `state.regenBaselinePopulate`; the build POST ships
  it back verbatim as `populate_baseline` plus `populate_touched`
  ([group, id, field] cells the user explicitly owned: cell commits,
  Set-all, preset Apply, LO re-solve — recorded regen-only, key join `|`).
  Server-side re-derivation was rejected: the merge source is the WORKING
  COPY, so a concurrent in-app edit would false-positive as a wizard edit
  and get clobbered. Regen mode never persists drafts ⇒ a refresh re-hydrates
  a fresh consistent baseline.
- **Changed rule** (`core/regen_populate.changed_fields`): changed iff
  present in both AND not close (floats `math.isclose(rel 1e-9, abs 1e-12)`),
  OR touched AND present. A CLEARED cell is NOT changed ("clear = don't
  re-seed": tier-1 keeps the calibration).
- **Fanout** (`regen_populate.protect_paths`): expands each changed cell to
  the concrete NEW-state dot-paths `run_build` writes — RF→`f_01`+`xy.RF`;
  LO→resolved port `upconverter_frequency`+`band`+`upconverters.1.frequency`
  (pointer chains via a path-returning resolver); pulses→the whole
  DragCosine family by name regex (`x90` etc. derive from the x180 seeds);
  pairs by MEMBERSHIP → the OLD pair id (the merge walks post-reconcile
  keys; existence checked under the NEW build's key); CR/ZZ via
  `cr_semantics` flavor maps. Absent paths drop out (harmless).
- **The one tier-1-should-win exception**: the LF z-port `delay` is derived
  from the xy band but user-overridable post-build (docs/31). On a
  band-crossing LO/band edit, `delay` is protected only if the old value
  equals the band table's (never hand-tuned); otherwise the old delay is
  kept and reported in `populate_conflicts` ("delay kept — verify").
  `regen_populate._BAND_TO_DELAY_NS` is pinned in sync with run_build's.
- **Merge**: `merge_states(..., protect_paths=None)` — in the leaf branch,
  pointer rule first (unchanged), then `path in protect` ⇒ NEW +
  `stats.populate_protected`. No kwarg ⇒ byte-identical legacy.
- **Amplifier fixes** (both used to fire on EVERY populate-step entry and
  would poison any diff): `autoApplyStandardDefaults` no-ops in regenerate
  mode (synthetic defaults must not appear as chip values);
  `applyLoAssignments` is FILL-ONLY-EMPTY in regen mode and never repaints a
  `data-dirty` (mid-typing) cell — the chip's real LOs stay. An explicit
  **Re-solve LOs** button (LO-map panel, regen-only) opts into the solver's
  picks and marks changed cells touched.
- Build-result panel: green "N populate edit(s) applied" chip +
  amber "N delay kept — verify" with the conflict lines.

Pinned by `tests/test_regen_populate.py` (rule/tolerance/cleared/touched,
fanout incl. LO chain + both delay branches + drag family + short-id pair
membership, merge integration, band-delay sync),
`test_regenerate.py` (end-to-end: edited value kept, untouched carried,
no-baseline legacy), the `/regenerate/build` pass-through pin in
`test_web.py`, and `tests/generate_regen_populate_selfcheck.cjs` (P1–P6).

## §3 — Band override (⓪-3)

The MW-FEM Nyquist bands OVERLAP (1: 0.05–5.5, 2: 4.5–7.5, 3: 6.5–10.5 GHz)
and both `bandOf` (JS) and `_band_for` (run_build) are first-match-wins —
measured on the SNU chip: every readout port is band 3 at LO 7.015 GHz, the
rebuild derived band 2 and the WRONG 161 ns LF delay (tier-1 masked the
state values, but the built band/delay were wrong; a structurally-new port
kept them). `_extract_populate` already extracted the real band — dead data.

- Populate gains a **band** column (qubit + resonator; select — / 1 / 2 / 3;
  stored as INT; empty = auto). `validate_spec` checks 1..3.
- `run_build._set_channel_lo(channel, lo, band=None)`: explicit band beats
  the derived pick; band-only edits (no LO change) go through the new
  `_set_channel_band`. The z delay derives from the (now correct) port band.
- Regen pre-fills the chip's real band; changing it rides populate-protect
  (with the delay conditional above).

Pinned by `tests/test_run_build_delay.py::TestBandOverride` + the JS↔Py
range-parity pins (unchanged — boundaries didn't move).

## §4 — Scripts export: default ON + path follows the state folder (⓪-4)

- The step-7 toggle now defaults ON (`d.scriptsEnabled !== false` migration:
  only an explicitly-saved false stays off).
- The scripts path **follows the output folder** as
  `<output>\state_gen_scripts` (separator inferred from the path dialect)
  until the user types in the box (`_scriptsPathTouched`); a draft/
  localStorage-restored non-empty path counts as touched.
- **`/regenerate/build` now honors `scripts_dir`** — it was ignored entirely
  (the bundle always landed in a hardcoded `<out>/build_scripts/`, the
  user's "build_script 폴더" report). `run_regenerate(scripts_dir=...)`
  writes the bundle there; absent ⇒ legacy location.

Pinned by `test_regenerate.py::test_scripts_dir_param_replaces_hardcoded_folder`
+ selfcheck P5.

## §5 — Qubit grid scale (⓪-5)

`wiring-grid.js` `CELL 40→52`, `STONE_R 15→20` (+30%; arrowheads/coupler
dots now scale off `STONE_R` instead of hardcoded px), static mirror
`topo-graph.js` `cell 36→46` / `R 13→17`, stone label fonts 0.667em→0.8em,
board max-heights 56→64vh (editable) / 44→52vh (populate mirror). The
topoboard selfchecks are px-free and stay green.
