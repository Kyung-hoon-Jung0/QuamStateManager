# QUAM coupler extensions

Install this package into the selected build environment:

```powershell
C:/ProgramData/miniconda3/envs/qsm_official_ext/python.exe -m pip install -e extensions/qsm_quam_ext
```

The State Manager process does not import this package. Its only declared
runtime dependencies are `quam` and `quam-builder`.

`FixedFrequencyTunableCouplerPair(FixedFrequencyTransmonPair)` adds an optional
`TunableCoupler`. `align()` and `wait(duration)` include every channel of both
qubits and the coupler; wait duration is in clock cycles.

`CouplerCZGate(QubitPairMacro)` requires `coupler_flux_pulse` (pulse or reference),
and stores `phase_shift_control`, `phase_shift_target`, `duration`, `extras`, and
`fidelity`. `apply()` plays only the coupler, with pair alignment and virtual Z
corrections. Keyword overrides are `phase_shift_control`, `phase_shift_target`,
`amplitude_scale_coupler`, and `duration`; auxiliary kwargs follow the official
macro convention. Phases are full turns; duration is in clock cycles. QUA phase
expressions are supported.

Builds keep pulses under `coupler.operations` with `id` equal to the operation
key. `macros.cz_coupler` references that pulse and `macros.cz` aliases the macro.
The root remains the official `FixedFrequencyQuam`.

Run the subprocess tests from the repository root:

```powershell
$env:PYTHONUTF8='1'
python -m pytest tests/test_cz_coupler_package.py -q -p no:cacheprovider
```

Live checks skip when the corresponding interpreter is absent. The base
environment is inspected and used for a refused build; nothing is installed
there. The package tests also cover node 34a phase kwargs and the no-argument
calls used by nodes 36, 37, and 39. Node applicability beyond these call
contracts is outside this package.
