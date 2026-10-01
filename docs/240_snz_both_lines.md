# docs/240: an SNZ CZ shows both flux lines from any row

2026-10-02. A review of the Pulses page found that a both-movers CZ
(`CZGateTwoFlux`, `SNZTwoFluxPulse`) showed different pictures depending on
which row was opened.

## Cause

- **Opened from the control slot (`flux_pulse_qubit`):** control and target
  were drawn on one plot.
- **Opened from the target slot (`flux_pulse_target`):** the target was drawn
  alone.

The cause was `_PAIR_MACRO_PULSE_RE`, the pattern that finds a pulse's
companions. It listed `flux_pulse_qubit|coupler_flux_pulse` but not
`flux_pulse_target`, while `GATE_SLOTS` and `_PULSE_PATH_RES` already listed
it. Because the same pattern names a section's role and builds its label, the
target section was also labelled "pair" rather than "target".

## Behaviour now

- **Either slot** opens both lines on one plot, each with its own edit section.
  - The roles read **control** and **target**.
  - `flux_pulse_qubit` reads "control" only when the macro really carries a
    target line. On a coupler chip it still reads "qubit", beside "coupler".
- **A qubit's `z.operations` entry that mirrors a slot** (e.g.
  `qubits.qA1.z.operations.cz_SNZ_flux_pulse_qA1_qA2`) opens with the slot's
  companions. The mirrored slot is the same pulse, so it is not drawn twice.
  - **A mirror is proven from the state, never guessed.** The `amplitude` must
    be a pointer into one pair-macro slot, and every absolute pointer among
    its fields must point into that same slot.
  - **These are not mirrors and open alone:** an entry with its own amplitude
    (a literal `0.5` beside pointed timings), and an entry whose fields point
    into two slots.
  - Channel operations that are not mirrors keep the docs/141 4k rule. They
    are alternatives, not companions, so they are never auto-drawn.

## Verified

- **Real Chrome** on a copy of the customer chip, with the env set to its lab
  stack, so the waveform is drawn by the lab's own `calculate_waveform()`:
  - Opening `qA2-qA1 cz_SNZ.flux_pulse_target` shows TARGET and CONTROL on one
    plot.
  - Opening the mirror `qA1.z.cz_SNZ_flux_pulse_qA1_qA2` shows the mirror plus
    CONTROL.
  - There are no JS errors.
- **Flask test client** on the same chip:
  - `cz_cosine_bipolar_pulse_qA1` (literal amplitude) still opens alone.
  - `cz_flattop_pulse_qA1` has no companion line, so it opens alone too.
- **Pins.**
  - `tests/test_pulse_snz_group.py` (7 tests): either slot, either mirror, the
    two not-a-mirror cases, and the coupler chip's roles unchanged.
  - All 8 mutations went red: target dropped from the pattern, no mirror
    lookup, the mirrored slot drawn twice, amplitude not required, two slots
    accepted, no target role, no control role, control for every
    `flux_pulse_qubit`.
