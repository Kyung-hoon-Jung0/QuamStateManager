"""A synthetic chip shaped like a modern quam_builder 0.4 export, for the RAM
(docs/2xx, P5/P6) staleness pins.

What matters is the SHAPE the incremental models must survive, not the
numbers: operations that alias each other (``"#./x180_DragCosine"``), relative
pointers (``"#../x180_DragCosine/length"``), channels that point into
``wiring`` which points into ``ports``, pairs whose control qubit is a pointer
to a whole qubit and whose CZ flux pulse lives in the qubit's ``z`` channel,
per-port bands/LOs shared between two qubits (the LO-peer annotation), an
LF-FEM ``output_mode``, confusion matrices, fidelity records, ``extras``, a
TWPA collection and a numeric-looking string. Deterministic for a seed.
"""
from __future__ import annotations

import random

DRAG = "quam.components.pulses.DragCosinePulse"
SQUARE = "quam.components.pulses.SquarePulse"
READOUT = "quam.components.pulses.SquareReadoutPulse"
QUBIT = "quam_builder.architecture.superconducting.qubit.flux_tunable_transmon.FluxTunableTransmon"
PAIR = "quam_builder.architecture.superconducting.qubit_pair.flux_tunable_transmon_pair.FluxTunableTransmonPair"


def build(n_qubits: int = 6, seed: int = 1) -> tuple[dict, dict]:
    rng = random.Random(seed)
    qubits: dict = {}
    wiring_q: dict = {}
    mw_out: dict = {"con1": {}}
    mw_in: dict = {"con1": {}}
    ao: dict = {"con1": {}}
    for i in range(1, n_qubits + 1):
        q = f"q{i}"
        fem = str(1 + (i - 1) // 4)           # 4 qubits per MW-FEM
        xy_port = str(2 + ((i - 1) % 4))      # ports 2..5, peers (2,3) / (4,5) share an LO
        mw_out["con1"].setdefault(fem, {})
        mw_out["con1"][fem][xy_port] = {
            "band": 2, "upconverter_frequency": 5.0e9 + 1e8 * (i % 2),
            "full_scale_power_dbm": -11 + (i % 3), "delay": 0,
            "__class__": "quam.components.ports.MWFEMAnalogOutputPort"}
        mw_out["con1"][fem]["1"] = {"band": 3, "upconverter_frequency": 7.2e9,
                                    "full_scale_power_dbm": -14}
        mw_in["con1"].setdefault(fem, {})["1"] = {
            "band": 3, "downconverter_frequency": "#/ports/mw_outputs/con1/%s/1/upconverter_frequency" % fem}
        lf = str(5 + (i - 1) // 8)
        ao["con1"].setdefault(lf, {})[str(1 + (i - 1) % 8)] = {
            "output_mode": "direct" if i % 3 else "amplified", "offset": 0.0,
            "upsampling_mode": "pulse"}
        amp = round(0.2 + 0.05 * rng.random(), 5)
        qubits[q] = {
            "id": q,
            "__class__": QUBIT,
            "f_01": 5.0e9 + 1e7 * i, "anharmonicity": -2.1e8 - 1e6 * i,
            "T1": 20e-6 + 1e-6 * i, "T2ramsey": 15e-6, "T2echo": 25e-6,
            "chi": 1.2e6, "grid_location": f"{(i - 1) % 3},{(i - 1) // 3}",
            "gate_fidelity": {"averaged": 0.998},
            "extras": {"note_id": "07", "calib": {"x": 1}},
            "xy": {
                "opx_output": f"#/wiring/qubits/{q}/xy/opx_output",
                "RF_frequency": 5.0e9 + 1e7 * i,
                "intermediate_frequency": "#./inferred_intermediate_frequency",
                "LO_frequency": "#./upconverter_frequency",
                "operations": {
                    "x180_DragCosine": {"__class__": DRAG, "length": 40,
                                        "amplitude": amp, "alpha": -0.4,
                                        "anharmonicity": "#../../../anharmonicity",
                                        "detuning": 0.0, "axis_angle": 0.0,
                                        "digital_marker": None},
                    "x90_DragCosine": {"__class__": DRAG,
                                       "length": "#../x180_DragCosine/length",
                                       "amplitude": round(amp / 2, 5), "alpha": -0.4,
                                       "anharmonicity": "#../../../anharmonicity",
                                       "detuning": "#../x180_DragCosine/detuning",
                                       "axis_angle": 0.0},
                    "x180": "#./x180_DragCosine",
                    "x90": "#./x90_DragCosine",
                    "saturation": {"__class__": SQUARE, "length": 20000,
                                   "amplitude": 0.25, "axis_angle": 0.0},
                },
            },
            "resonator": {
                "opx_output": f"#/wiring/qubits/{q}/rr/opx_output",
                "opx_input": f"#/wiring/qubits/{q}/rr/opx_input",
                "time_of_flight": 280, "depletion_time": 1000,
                "f_01": 7.1e9 + 1e6 * i, "RF_frequency": 7.1e9 + 1e6 * i,
                "confusion_matrix": [[0.97, 0.03], [0.05, 0.95]],
                "operations": {
                    "readout": {"__class__": READOUT, "length": 1500,
                                "amplitude": round(0.05 + 0.01 * i, 4),
                                "axis_angle": 0.0, "threshold": 0.001,
                                "rus_exit_threshold": 0.0},
                },
            },
            "z": {
                "opx_output": f"#/wiring/qubits/{q}/z/opx_output",
                "joint_offset": 0.01 * i, "min_offset": 0.0,
                "operations": {
                    "const": {"__class__": SQUARE, "length": 100, "amplitude": 0.1},
                },
            },
            "freq_vs_flux_01_quad_term": -1.1e10,
        }
        wiring_q[q] = {
            "xy": {"opx_output": f"#/ports/mw_outputs/con1/{fem}/{xy_port}"},
            "rr": {"opx_output": f"#/ports/mw_outputs/con1/{fem}/1",
                   "opx_input": f"#/ports/mw_inputs/con1/{fem}/1"},
            "z": {"opx_output": f"#/ports/analog_outputs/con1/{lf}/{1 + (i - 1) % 8}"},
        }
    pairs: dict = {}
    for i in range(1, n_qubits):
        a, b = f"q{i}", f"q{i + 1}"
        p = f"{a}-{i + 1}"
        cz_op = f"cz_flattop_{a}_{b}"
        qubits[a]["z"]["operations"][cz_op] = {
            "__class__": SQUARE, "length": 48, "amplitude": round(-0.12 - 0.01 * i, 4)}
        pairs[p] = {
            "id": p, "__class__": PAIR,
            "qubit_control": f"#/qubits/{a}", "qubit_target": f"#/qubits/{b}",
            "moving_qubit": "control", "coupler": None, "detuning": 1.5e6 * i,
            "confusion": [[0.95, 0.02, 0.02, 0.01], [0.03, 0.94, 0.02, 0.01],
                          [0.02, 0.02, 0.94, 0.02], [0.01, 0.02, 0.03, 0.94]],
            "extras": {"cz_branch": "02"},
            "macros": {
                "cz": "#./cz_flattop",
                "cz_flattop": {
                    "__class__": "quam_builder.architecture.superconducting.custom_gates.flux_tunable_transmon_pair.two_qubit_gates.CZGate",
                    "flux_pulse_qubit": f"#/qubits/{a}/z/operations/{cz_op}",
                    "phase_shift_control": 0.1 * i, "phase_shift_target": -0.05 * i,
                    "fidelity": {"StandardRB": {"error_per_gate": 0.01, "fidelity": 0.99},
                                 "InterleavedRB": {"fidelity": 0.98 + 0.001 * i}},
                },
            },
        }
    state = {
        "qubits": qubits,
        "qubit_pairs": pairs,
        "twpas": {"twpa1": {"id": "twpa1", "pump_frequency": 6.0e9,
                            "pump_amplitude": 0.3, "spectroscopy_amplitude": "0.12"}},
        "active_qubit_names": list(qubits),
        "active_qubit_pair_names": list(pairs),
        "ports": {"mw_outputs": mw_out, "mw_inputs": mw_in, "analog_outputs": ao},
        "extras": {"chip_name": "ramchip"},
        "__class__": "quam_config.my_quam.Quam",
        "__package_versions__": {"quam": "0.6.0"},
    }
    wiring = {"wiring": {"qubits": wiring_q},
              "network": {"host": "127.0.0.1", "cluster_name": "c"}}
    return state, wiring
