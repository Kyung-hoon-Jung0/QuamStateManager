"""A synthetic N-qubit chip (state.json + wiring.json) for scale tests.

Not real data: a 10-wide grid of flux-tunable transmons, one readout
feedline per grid row, drives on MW-FEM outputs (the feedline's own FEM
first, then spare MW-FEMs), flux on LF-FEM outputs, and a CZ pair for every
horizontal and vertical neighbour (100 qubits -> 180 pairs). Each pair's
moving qubit carries a z operation that reads the pair's macro (the shape a
lab's CZ setup has), so a copy of a pair has member operations to carry.

    python tests/make_synthetic_chip.py OUT_DIR [N_QUBITS]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_P = "quam.components.pulses."
_B = "quam_builder.architecture.superconducting."
WIDTH = 10
DRIVES_PER_FEED_FEM = 7          # outputs 2..8 of the feedline's MW-FEM


def _pulse(cls, **kw):
    d = {"length": 40, "id": None, "digital_marker": None, "axis_angle": 0}
    d.update(kw)
    d["__class__"] = _P + cls
    return d


def _qubit(qid, col, row, f01, frr):
    xy_ops = {"x180": "#./x180_DragCosine", "x90": "#./x90_DragCosine",
              "x180_DragCosine": _pulse("DragCosinePulse", amplitude=0.2, alpha=-1.0,
                                        anharmonicity=200e6, detuning=0),
              "x90_DragCosine": _pulse("DragCosinePulse", amplitude=0.1, alpha=-1.0,
                                       anharmonicity=200e6, detuning="#../x180_DragCosine/detuning"),
              "saturation": _pulse("SquarePulse", length=20000, amplitude=1)}
    return {
        "id": qid, "f_01": f01, "f_12": f01 - 200e6, "anharmonicity": 200e6,
        "T1": 3e-5, "T2ramsey": 2e-5, "T2echo": 4e-5, "grid_location": f"{col},{row}",
        "gate_fidelity": {"averaged": 0.999}, "extras": {},
        "xy": {"operations": xy_ops, "RF_frequency": f01,
               "intermediate_frequency": "#./inferred_intermediate_frequency",
               "LO_frequency": "#./upconverter_frequency", "upconverter": 1,
               "opx_output": f"#/wiring/qubits/{qid}/xy/opx_output", "thread": qid,
               "__class__": _B + "components.xy_drive.XYDriveMW"},
        "resonator": {"operations": {"readout": _pulse("SquareReadoutPulse", length=1000,
                                                       amplitude=0.01, threshold=0.0)},
                      "RF_frequency": frr, "f_01": frr, "confusion_matrix": [[0.97, 0.03], [0.04, 0.96]],
                      "intermediate_frequency": "#./inferred_intermediate_frequency",
                      "LO_frequency": "#./upconverter_frequency", "upconverter": 1,
                      "time_of_flight": 300, "thread": qid,
                      "opx_output": f"#/wiring/qubits/{qid}/rr/opx_output",
                      "opx_input": f"#/wiring/qubits/{qid}/rr/opx_input",
                      "__class__": _B + "components.readout_resonator.ReadoutResonatorMW"},
        "z": {"operations": {"const": _pulse("SquarePulse", amplitude=0.1)},
              "joint_offset": 0.01, "independent_offset": 0, "min_offset": 0,
              "opx_output": f"#/wiring/qubits/{qid}/z/opx_output",
              "__class__": _B + "components.flux_line.FluxLine"},
        "__class__": _B + "qubit.flux_tunable_transmon.FluxTunableTransmon",
    }


def make_chip(n: int = 100) -> tuple[dict, dict]:
    rows = (n + WIDTH - 1) // WIDTH
    ports = {"mw_outputs": {}, "mw_inputs": {}, "analog_outputs": {}, "__class__": "quam.components.ports.FEMPortsContainer"}
    wq: dict = {}
    qubits: dict = {}
    slot = {"con": 1, "fem": 1}

    def next_fem():
        con, fem = slot["con"], slot["fem"]
        slot["fem"] += 1
        if slot["fem"] > 8:
            slot["con"] += 1
            slot["fem"] = 1
        return f"con{con}", fem

    def mw_out(con, fem, pid, lo, band=2):
        ports["mw_outputs"].setdefault(con, {}).setdefault(str(fem), {})[str(pid)] = {
            "controller_id": con, "fem_id": fem, "port_id": pid, "band": band,
            "upconverter_frequency": lo, "upconverters": None, "delay": 0, "shareable": False,
            "sampling_rate": 1e9, "full_scale_power_dbm": -2,
            "__class__": "quam.components.ports.analog_outputs.MWFEMAnalogOutputPort"}

    spare = []                    # (con, fem, pid) drive outputs on spare MW-FEMs
    ids = [f"q{i + 1}" for i in range(n)]
    for r in range(rows):
        con, fem = next_fem()
        frr_lo = 7.2e9 + 0.05e9 * r
        mw_out(con, fem, 1, frr_lo)
        ports["mw_inputs"].setdefault(con, {}).setdefault(str(fem), {})["1"] = {
            "controller_id": con, "fem_id": fem, "port_id": 1, "band": 2,
            "downconverter_frequency": frr_lo, "sampling_rate": 1e9, "shareable": False,
            "__class__": "quam.components.ports.analog_inputs.MWFEMAnalogInputPort"}
        members = ids[r * WIDTH:(r + 1) * WIDTH]
        for i, qid in enumerate(members):
            col = i
            f01 = 5.0e9 + 0.02e9 * ((i + r) % 10)
            frr = frr_lo + 0.1e9 + 0.01e9 * i
            qubits[qid] = _qubit(qid, col, r, f01, frr)
            if i < DRIVES_PER_FEED_FEM:
                d = (con, fem, i + 2)
            else:
                if not spare:
                    sc, sf = next_fem()
                    spare.extend((sc, sf, p) for p in range(1, 9))
                d = spare.pop(0)
            mw_out(d[0], d[1], d[2], f01 - 100e6)
            wq[qid] = {"rr": {"opx_output": f"#/ports/mw_outputs/{con}/{fem}/1",
                              "opx_input": f"#/ports/mw_inputs/{con}/{fem}/1"},
                       "xy": {"opx_output": f"#/ports/mw_outputs/{d[0]}/{d[1]}/{d[2]}"}}
    lf = []
    for qid in ids:
        if not lf:
            c, f = next_fem()
            lf.extend((c, f, p) for p in range(1, 9))
        c, f, p = lf.pop(0)
        ports["analog_outputs"].setdefault(c, {}).setdefault(str(f), {})[str(p)] = {
            "controller_id": c, "fem_id": f, "port_id": p, "offset": None, "delay": 0,
            "crosstalk": {}, "feedforward_filter": None, "shareable": False,
            "sampling_rate": 1e9, "upsampling_mode": "pulse", "output_mode": "direct",
            "__class__": "quam.components.ports.analog_outputs.LFFEMAnalogOutputPort"}
        wq[qid]["z"] = {"opx_output": f"#/ports/analog_outputs/{c}/{f}/{p}"}

    pairs: dict = {}
    for k, qid in enumerate(ids):
        r, c = divmod(k, WIDTH)
        for nb in ((r, c + 1), (r + 1, c)):
            j = nb[0] * WIDTH + nb[1]
            if nb[1] >= WIDTH or j >= n:
                continue
            t = ids[j]
            pid = f"{qid}-{t}"
            op = f"cz_flattop_pulse_{t}"
            pairs[pid] = {
                "id": pid, "qubit_control": f"#/qubits/{qid}", "qubit_target": f"#/qubits/{t}",
                "moving_qubit": "control", "coupler": None,
                "macros": {"cz": "#./cz_flattop", "cz_flattop": {
                    "id": "#./inferred_id", "fidelity": {"Bell_State": {"Fidelity": 0.95}},
                    "flux_pulse_qubit": _pulse("SquarePulse", length=64, amplitude=0.2, id=op),
                    "phase_shift_control": 0.1, "phase_shift_target": 0.2,
                    "__class__": _B + "custom_gates.flux_tunable_transmon_pair.two_qubit_gates.CZGate"}},
                "extras": {},
                "__class__": _B + "qubit_pair.flux_tunable_transmon_pair.FluxTunableTransmonPair"}
            qubits[qid]["z"]["operations"][op] = {
                "length": f"#/qubit_pairs/{pid}/macros/cz_flattop/flux_pulse_qubit/length",
                "amplitude": f"#/qubit_pairs/{pid}/macros/cz_flattop/flux_pulse_qubit/amplitude",
                "id": None, "digital_marker": None, "axis_angle": None,
                "__class__": _P + "SquarePulse"}
    state = {"qubits": qubits, "qubit_pairs": pairs,
             "active_qubit_names": list(ids), "active_qubit_pair_names": list(pairs),
             "ports": ports, "extras": {},
             "__class__": "quam_builder.architecture.superconducting.qpu.FluxTunableQuam"}
    wiring = {"wiring": {"qubits": wq}, "network": {"host": "127.0.0.1", "cluster_name": "synthetic"}}
    return state, wiring


def write_chip(out: str | Path, n: int = 100) -> Path:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    s, w = make_chip(n)
    (out / "state.json").write_text(json.dumps(s, indent=1), encoding="utf-8")
    (out / "wiring.json").write_text(json.dumps(w, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    write_chip(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 100)
