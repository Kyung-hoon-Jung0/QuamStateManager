# 53 — qop37 alignment (quam 0.6.0 / quam_builder 0.4.0)

Verification + fixes making GENERATE / MODIFY / DIAGNOSTICS work end-to-end
from a modern QM stack (probed live against the `qop37_new` conda env:
quam 0.6.0, quam_builder 0.4.0, qualang_tools 0.22.0, qm 1.3.1).

## Ground truth (probed, not assumed)

Class moves between quam 0.5.0a3 (our transcription source) and this stack:

| Class | quam ≤0.5 home | 0.6.0/0.4.0 home |
|---|---|---|
| `SNZPulse`, `ErfSquarePulse`, `GaussianFilteredSymmetricBipolarPulse` | `quam.components.pulses` | `quam_builder.architecture.superconducting.components.pulses` |
| `GaussianFilteredSquarePulse` | `quam.components.pulses` | `quam_builder.common.pulses` |
| 13 core classes (Square/Gaussian/Drag/FlatTop/…) | `quam.components.pulses` | unchanged — **but** quam_builder additionally defines its own duplicates (Drag* etc. in the architecture module; Gaussian/FlatTopGaussian/FlatTopCosine in `common.pulses`), and fresh builds write those paths |

Field changes: `GaussianFiltered*` renamed `post_zero_padding_length` →
`padding_length` (identical math); `_FlatTopGaussianPulse` dropped `sigma`
(was already ignored). Gate landscape: `ParametricCZGate` **removed
entirely**; `CZGate` survives (gains `duration_qubit`, loses
`duration_control`/`moving_qubit`); `CRGate` + `StarkInducedCZGate` exist at
`custom_gates.fixed_transmon_pair`; `moving_qubit` lives on
`FluxTunableTransmonPair`; the root `Quam` keeps `active_twpa_names`/`twpas`.
Legacy qpu module paths renamed (`qpu.flux_tunable` →
`qpu.flux_tunable_quam`; the `qpu` package re-exports both classes).

**Waveform formulas: bit-identical everywhere that matters.** All 63 golden
cases match at rtol=0 — from quam 0.6.0, from the quam_builder homes of the
moved classes, and from quam_builder's own 1Q duplicates. `padding_length`
is a pure rename. The only drift: quam 0.6.0's two *deprecated* classes
(`_FlatTopGaussianPulse`/`_CosineBipolarPulse`) moved zero-padding from
centered to trailing — while quam_builder's replacement
`SmoothedFlatTopGaussianPulse`/`SmoothedCosineBipolarPulse` keep the OLD
centered semantics bit-for-bit. **Decision: the committed golden and our
synth keep the 0.5.0a3 centered semantics** — new chips use the Smoothed /
quam_builder classes which match exactly; only a legacy `_`-class chip
*executed* on quam 0.6.0 would render ≤4-sample-shifted padding in our
preview (Verify-vs-config is the check).

## Fixes (all live-verified against qop37_new)

1. **`run_build._pulse_class`** — dual-home class resolver (`_PULSE_HOMES`:
   quam → quam_builder arch → quam_builder common). Before: SNZ /
   flattop_erf CZ variants silently degraded to unipolar. After: genuine
   `SNZPulse`/`ErfSquarePulse` chips build with zero warnings and pass
   `generate_config()`. The `_seed_cr_gate` import uses the same resolver.
2. **`probe_capabilities`** — `pulse.cz_*`/`pulse.cr_flattop` locators are
   `any_module` over the same `_PULSE_HOMES` (pinned in sync by
   `TestPulseHomesInSync`). Before: the wizard report card said "upgrade
   quam" for SNZ/Erf on an env that fully has them. `capabilities.py` fix
   strings now name both packages.
3. **`pulse_catalog` homes** — the quam_builder module homes are registered
   in `_BY_QCLASS` (resolve as `exact`, since golden-verified), Smoothed*
   classes alias to the deprecated specs, and **`chip_qclass` only emits a
   "prefix" write when `prefix + key` is a registered home** — quam_builder
   scatters classes across modules, and a guessed unhomed path (e.g.
   `…architecture….GaussianPulse`) makes `Quam.load` fail on the whole file.
4. **`padding_length` alias** — `ParamSpec.aliases`; the GaussianFiltered*
   specs accept the renamed field in synthesis, inferred length, unmodeled
   detection, and `spec.param()` lookup (preview length was silently wrong
   by the padding amount before).
5. **`cz_parametric` is evidence-gated** — offered/accepted only when the
   chip already carries a `ParametricCZGate` macro to copy the exact class
   path from (form drops the option; POST returns 409 otherwise).
6. **TWPA** — `run_build` strips a redundant `twpa` prefix from spec ids
   (qualang_tools renders `f"twpa{id}"`, so `twpa1` used to become
   `twpatwpa1`), and `regen_spec` accepts the short wiring keys `p`/`i`
   this stack writes (TWPA was silently lost on re-generate).
7. **`run_generate_config._load_machine`** — package-attr fallbacks
   (`qpu` + `FluxTunableQuam`/`FixedFrequencyQuam`) so legacy-marker chips
   still load on 0.4.0's renamed module layout.
8. **Emitted regen recipe** — the pairs step degrades with a WARNING when
   the calibration repo ships no `pair_gates.py` (this env's qua-libs
   checkout doesn't), instead of crashing before `machine.save()`.
9. **`run_waveform_golden`** — resolves classes across `_PULSE_HOMES` and
   adapts renamed/removed kwargs, so ground truth can be dumped from a
   modern env (63/63 cases run; 58 bit-identical, 5 documented quam-0.6.0
   deprecated-semantics diffs).

## End-to-end status on qop37_new

- **GENERATE**: allocate + build for unipolar / flattop / bipolar / SNZ /
  flattop_erf, TWPA lines, `generate_config()` preview — all green, zero
  warnings.
- **MODIFY**: a generated chip loads with every pulse `known` (`exact`
  provenance for the quam_builder paths), correct inferred lengths,
  sparklines, create/edit/rename/duplicate/delete verified; created pulses
  carry the chip's own (importable) class paths.
- **DIAGNOSTICS**: `lint_state` on a generated chip → 0 findings, 0 crashes,
  0 fabricated waveform errors.

Capability report on this env: 26/27 available; only `pair.cz_parametric`
missing — which is the truth.
