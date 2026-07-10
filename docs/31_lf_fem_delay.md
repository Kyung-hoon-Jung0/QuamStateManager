# 31: Auto-set LF-FEM `delay` from the paired MW-FEM band

> When the generator writes `state.json` it now auto-fills the
> `delay` field on every LF-FEM analog output from the paired qubit's
> MW-FEM band, so flux pulses arrive aligned with MW drive without any
> manual tuning. Researchers can still override the value per port from
> the qubit / pair detail page after generation.
>
> **Read this if you are:** touching `generator/run_build.py`, the
> qubit / pair detail property maps, or the wizard's Populate step.
>
> **Status:** v1 shipped on `feat/lf-fem-delay-auto`.

---

## Why this exists

OPX1000 hardware processes MW-FEM signals through more stages than the
LF-FEM, which means an MW-FEM output is delayed relative to an LF-FEM
output of the same start time:

| MW-FEM band the paired flux must align with | LF-FEM `delay` |
|---|---|
| Band 1 (50 MHz – 5.5 GHz)  | **141 ns** |
| Band 2 (4.5 GHz – 7.5 GHz) | **161 ns** |
| Band 3 (6.5 GHz – 10.5 GHz)| **141 ns** |

Before this change, the generator wrote `delay: 0` on every LF-FEM
output. Researchers had to either hand-patch `state.json` after
generation or accept ~150 ns of misalignment in two-qubit gates and
flux-tuned single-qubit experiments. The reference QM config file the
user provided
(`1Q_2Q_calibrations/.../1Q_11_power_rabi.py`-style setup) explicitly
sets `"delay": 141 * u.ns` on the LF-FEM output and documents the band
rule in a comment block at the top.

## How it works

### Generation time — `_apply_qubit` and `_apply_pairs`

`generator/run_build.py` has a module-level map:

```python
_BAND_TO_DELAY_NS = {1: 141, 2: 161, 3: 141}
```

`_apply_qubit` already calls `_set_channel_lo(xy, LO_freq)` which
computes the MW-FEM band via `_band_for(freq)` and writes it to
`xy.opx_output.band`. Immediately after that, we read the band back
and apply it to the same qubit's z line:

```python
z = getattr(qubit, "z", None)
if z is not None:
    z_port = getattr(z, "opx_output", None)
    xy_port = getattr(xy, "opx_output", None)
    band = getattr(xy_port, "band", None)
    _apply_lf_delay(z_port, band)
```

`_apply_pairs` does the same for couplers, picking the **moving
qubit's** xy band (the qubit whose z is also playing during the 2Q
gate — they share the timing reference):

```python
moving_xy_port = moving_q.xy.opx_output if moving_q else None
coupler_band = getattr(moving_xy_port, "band", None)
_apply_lf_delay(coupler.opx_output, coupler_band)
```

`_apply_lf_delay` is the safe setter — it no-ops on missing ports,
unknown bands, or QUAM `ValueError` (which the port raises when the
target is a reference rather than a concrete attribute).

### Post-generation — inline editing on detail pages

The delay lives at `qubits.<id>.z.opx_output.delay` (and
`qubit_pairs.<id>.coupler.opx_output.delay`) in `state.json`. Two new
rows in the detail-page property maps surface it:

```python
# routes.py: _QUBIT_PROPERTY_MAP
("Flux", "z_delay_ns", "qubits.{name}.z.opx_output.delay"),
# routes.py: _PAIR_PROPERTY_MAP
("Coupler", "coupler_delay_ns", "qubit_pairs.{name}.coupler.opx_output.delay"),
```

`QueryEngine.get_qubit()` and `.get_pair()` flatten the value out as
`z_delay_ns` / `coupler_delay_ns`. The existing inline-edit form
(`POST /qubit/<id>/edit` and `POST /pair/<id>/edit`) handles the
mutation — a researcher clicks the value, types a new integer, presses
Enter. `Modifier.set_value` persists it, the Config Viewer (`docs/30`)
will reflect it on the next regenerate.

### Wizard preview — Populate step chip

`web/static/generate.js` mirrors `_BAND_TO_DELAY_NS` and renders a
read-only chip strip below the flux Populate table:

> LF-FEM `delay` (auto-set at generation, editable per-qubit afterward):
>
> `qA1` · **141 ns** (band 1)  `qA2` · **161 ns** (band 2) ...

The chip refreshes whenever `recomputeLOs()` runs (bands change as
researchers enter RF frequencies). It is intentionally read-only — the
real editing UX is on the post-generation detail page.

## Override path

There is **no spec-time override field**. If your chip needs a
different delay (long cable runs, intentional skew between qubits,
etc.), generate first, then edit the per-port value from:

- the qubit detail page (Flux section → `z_delay_ns`), or
- the pair detail page (Coupler section → `coupler_delay_ns`).

Save (writes the working copy), then Apply to Live when you're ready
for an experiment script to pick up the new value.

## Edge cases

- **No xy line on a qubit (flux-only)** — the band can't be derived,
  so `_apply_lf_delay` no-ops and the existing `delay: 0` default
  persists. Fix manually via the detail page if needed.
- **Coupler with a fixed-frequency pair** — same: no MW-FEM band on
  the qubit's xy means no auto-delay. Edit manually.
- **Reference assignment on the QUAM port** — some QUAM port objects
  raise `ValueError` when their `delay` is a JSON reference rather
  than a real attribute. The helper catches that and moves on; the
  underlying reference target keeps whatever value it had.
- **Bands 6.5 – 7.5 GHz** — both band 2 and band 3 cover this range.
  `_band_for()` matches band 2 first (its check comes earlier), so
  these qubits get 161 ns. Same behaviour as the JS wizard mirror.

## Files changed

- `quam_state_manager/generator/run_build.py` — `_BAND_TO_DELAY_NS`,
  `_delay_for_band`, `_apply_lf_delay`, calls from `_apply_qubit` /
  `_apply_pairs`.
- `quam_state_manager/web/routes.py` — `z_delay_ns` row in
  `_QUBIT_PROPERTY_MAP`, `coupler_delay_ns` row in
  `_PAIR_PROPERTY_MAP`.
- `quam_state_manager/core/query.py` — `get_qubit()` /
  `get_pair()` surface the new keys.
- `quam_state_manager/web/static/generate.js` — `BAND_TO_DELAY_NS`,
  `renderFluxDelaySummary`, called from `recomputeLOs` /
  `renderPopulateTables`.
- `quam_state_manager/web/static/style.css` — chip CSS.
- `tests/test_run_build_delay.py` — 22 unit tests for the helpers
  (loads the module without invoking the QM stack).
- `tests/test_web.py::TestLfFemDelayRows` — 5 route tests covering the
  new editable rows + the `QueryEngine` flatten.

## Verification

- Unit tests: `pytest tests/test_run_build_delay.py` (22 pass).
- Route tests: `pytest tests/test_web.py::TestLfFemDelayRows` (5 pass).
- Full regression sweep over touched modules: 510 pass / 0 fail.
- Manual (requires a QM-stack env): generate a `quam_state`
  via the wizard with mixed-band qubits, confirm
  `qubits.<id>.z.opx_output.delay` lands as 141 / 161 per band, then
  open the Config Viewer (`docs/30`) and confirm the LF-FEM
  controllers block shows the new delay. Edit the value inline on the
  qubit detail page, Save, regenerate the Config Viewer, confirm the
  edit propagated.
- True acceptance test (out of automated scope): run
  `1Q_11_power_rabi.py` against the generated state and confirm flux +
  MW pulses align on the scope to within ±1 ns.
