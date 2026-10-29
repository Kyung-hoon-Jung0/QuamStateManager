"""Official-environment oracle. All changes are confined to the supplied copy."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace


def main():
    rigs, destination = map(Path, sys.argv[1:])
    source = next(p for p in rigs.glob('sm_*') if (p / 'state.json').is_file())
    shutil.copytree(source, destination)
    sys.path.insert(0, str(rigs / 'qua-libs' / 'qualibration_graphs' / 'superconducting'))
    from quam_builder.architecture.superconducting.qpu.flux_tunable_quam import FluxTunableQuam
    from calibration_utils.cz_iswap_flux_bootstrap.parameters import (
        QubitRoles, estimate_qubit_flux_shift, verify_moving_qubit)

    with contextlib.redirect_stdout(io.StringIO()):
        machine = FluxTunableQuam.load(destination)
        for i, q in enumerate(machine.qubits.values()):
            q.freq_vs_flux_01_quad_term = -1e12
            q.anharmonicity = -(50e6 if i % 2 else 2e9)
            q.xy.RF_frequency = q.f_01
            q.z.flux_point = 'joint'
        answers = []
        branches = set()
        for pair, qp in machine.qubit_pairs.items():
            for op, macro in qp.macros.items():
                if type(macro).__name__ != 'CZGate':
                    continue
                roles = QubitRoles.resolve(qp, 'cz')
                role = 'control' if roles.moving is qp.qubit_control else 'target'
                branches.add('high' if roles.moving is roles.high else 'low')
                pulse = macro.flux_pulse_qubit
                if pulse.id is None:
                    pulse.id = next((k for k, p in roles.moving.z.operations.items() if p is pulse), op)
                verify_moving_qubit(qp, 'cz', op, repair_routing=True, log_callable=lambda s: None)
                # Capture the official helper's actual detuning local on return.
                # There is no separate detuning helper in this revision.
                captured = {}
                def profile(frame, event, arg):
                    if event == 'return' and frame.f_code is estimate_qubit_flux_shift.__code__:
                        captured.update(frame.f_locals)
                sys.setprofile(profile)
                try:
                    amplitude = estimate_qubit_flux_shift(
                        SimpleNamespace(use_saved_detuning=False, cz_or_iswap='cz'), qp, lambda s: None)
                finally:
                    sys.setprofile(None)
                # Positive stored polarity isolates the signed node convention.
                pulse.amplitude = abs(pulse.amplitude)
                answers.append(dict(pair=pair, op=op, mover=role,
                                    delta_hz=-captured['detuning_hz'], amplitude_v=amplitude))
        machine.save(destination)
    print(json.dumps(dict(answers=answers, branches=sorted(branches))))


if __name__ == '__main__':
    main()
