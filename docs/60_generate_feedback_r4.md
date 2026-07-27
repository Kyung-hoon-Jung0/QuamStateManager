# 60 — Generate wizard feedback r4 (supercritical batch)

Three customer-critical items on the Generate-Config wizard.

## 1. Qubit delete on the board is now recoverable

**Was:** selecting a stone and hitting Del/Backspace ran
`wiring-grid.removeQubit` — splicing the qubit, filtering every incident pair,
deleting all its populate physics — with **zero undo coverage**: the wizard's
Ctrl+Z stack only snapshots DOM fields, the count-field "recovery" regenerates
a bare id with no placement/physics/pairs, and the Renumber affordance cements
the loss. Users restarted the wizard.

**Now:** `removeQubit` snapshots everything it destroys (id + list position,
per-group populate, incident pairs with positions, pair-physics buckets) onto
a 20-deep undo stack.

- **Board affordance:** the status row shows a persistent
  "qN deleted — [Undo] (or Ctrl+Z)" until undone or superseded; a toast fires
  on delete.
- **Ctrl+Z integration:** each board delete pushes a `{boardUndo: true}`
  sentinel into the wizard's `_wizStack`, so undo ordering interleaves
  CORRECTLY with field edits ("edit field → delete qubit → Ctrl+Z" restores
  the qubit first). Stale sentinels (delete already undone via the button)
  skip harmlessly.
- **Restore semantics:** qubit returns at its original index with placement +
  physics; an incident pair is only resurrected when its OTHER member exists
  (undoing q3 while q4 is still deleted must not create a half-dangling
  q3-q4); tolerates the count field having re-created the bare id meanwhile.

Pinned by `tests/wiring_undo_selfcheck.cjs` (jsdom, 17 checks — snapshot
round-trip byte-equality, LIFO stacking, partner-gating, empty-stack no-op),
driven by `test_generate_feedback_r4.py`.

## 2. Every CZ gate seeds by default, pre-filled

**Was:** one `cz_variant` per pair (blank == unipolar), every populate cell
blank — users filled each pair from scratch.

**Now:**

- `cz_variant` default is **"all"** (blank == "all" for old drafts):
  `run_build._finalize_pair_gates` seeds **every** variant in `_CZ_VARIANTS`
  (`cz_unipolar/cz_flattop/cz_bipolar/cz_SNZ/cz_flattop_erf` macros + their
  per-variant flux/coupler ops) from the same per-pair seeds. A variant whose
  pulse class is missing from the env **skips with a warning**
  (`fallback=False`) — never the old silent collapse onto unipolar N times;
  unipolar (core SquarePulse) always seeds, so a build cannot hard-fail on an
  optional shape. Explicit single variants keep the old fallback behavior.
- `validate_spec` accepts `"all"`; `required_capabilities` treats blank/"all"
  as adding **no** shape requirements (skip-not-fail ⇒ nothing to block on);
  an explicit variant still requires its class.
- **Auto-prefill:** entering the Populate step applies the built-in
  "Standard defaults" preset ONCE per draft (fill-only-empty, flag persisted
  in the draft so a deliberately cleared cell stays cleared). The builtin
  gained the ZZ drive seeds (`zz_drive_amplitude 1.0`, `zz_flattop_length
  100`, `zz_flattop_flat_length 84`) — run_build's own defaults, never
  invented numbers.
- Script-emitter golden (`tests/golden/scripts_bundle_cz/02_build_machine.py`)
  regenerated — it byte-pins the `inspect.getsource`-extracted run_build
  machinery, which changed here by design.

## 3. 2Q gate pulse shapes are visible in the wizard

- **Step-8 "Preview config" gallery:** new routes
  `/generate/preview-pulses` + `/generate/preview-pulse-waveform` serve, from
  the already-stashed preview seed, every 2Q-gate op
  (`config_view.all_pair_gate_operations` — dedicated `cr_/zz_/coupler_/cz_`
  elements plus gate-named ops on qubit channels) and its waveform traces
  (`waveform_for_element_op`, the config's own sample arrays — identical
  payload shape to the Config Viewer's pair-waveform route). The wizard
  renders a click-to-plot gallery under the Preview-config result.
- **Step-6 live-preview fixes:** ZZ cells now preview the flattop ZZ drive
  run_build actually seeds (previously fell through to a misleading CR
  square); `coupler_interaction_offset` previews the COUPLER-side pulse
  (square/flat-top mirror for unipolar/flattop/bipolar; honestly no preview
  for SNZ/erf, which carry no coupler pulse); blank/"all" variants preview
  the unipolar representative with an explicit "(unipolar shown)" title.

## Tests

`tests/test_generate_feedback_r4.py` (cjs driver, validate/capabilities/
builtin pins, gallery helpers + routes incl. 409/404 gates),
`tests/test_pair_gates_seed.py` (REAL QM-env builds: default seeds the
available library, explicit single stays scoped), regenerated golden bundle,
`test_script_emitter` + `test_gen_presets` + `test_generate_power` green.
