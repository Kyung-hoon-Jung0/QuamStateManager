"""Shared synthetic CR/ZZ chip fixtures — one source of truth for the three
quam-builder CR schema flavors (see ``core/cr_semantics.py`` module docstring
and ``docs/54_cr_integration.md``).

Builders return ``(state, wiring)`` dicts shaped after the real artifacts:

* :func:`make_flavor_a`  — ``lo_if`` (a08bf66 / wizard-built ``gen_2x3_cr``):
  target LO+IF literals, ``bell_state_fidelity``, dedicated CR port per pair
  on a second FEM, exhaustive-null serialization, one direction per edge.
* :func:`make_flavor_b`  — ``rf`` (fa540b6 / ALL customer artifacts): RF
  pointer to the target's xy, shared control-xy port with the dual-upconverter
  layout, BOTH directions per edge, target-side cancel stubs, ``sparse=True``
  strips null keys + bares the macro (the CR_state serialization),
  ``with_zz=True`` adds the Stark-CZ family (zz_drive + xy_detuned + macro).
* :func:`make_flavor_c`  — ``rf_drive`` (branch tip c119d62) — PROVISIONAL:
  no real artifact exists; re-pin the moment a tip-built state exists (risk R1).
* :func:`make_cz_reference` — a small flux-CZ chip for no-op/regression pins.

Frequency plan (flavor B) — round numbers so effective-IF math is exact:

====  ======  =====  =====   pair   ctrl  tgt   IF
q     f_01    LO1    LO2     q0-1   q0    q1    +200 MHz  (5.2 − 5.0)
q0    4.9e9   5.0e9  5.0e9   q1-0   q1    q0    −50 MHz   (4.9 − 4.95)
q1    5.2e9   5.3e9  4.95e9  q1-2   q1    q2    +50 MHz   (5.0 − 4.95)
q2    5.0e9   5.1e9  4.75e9  q2-1   q2    q1    +450 MHz  ← deliberately > 400
====  ======  =====  =====

Network values are the repo's scrub constants (``127.0.0.1`` / ``my_cluster``)
— never real cluster identity.
"""

from __future__ import annotations

import json
from pathlib import Path

_COMP = "quam_builder.architecture.superconducting.components."
_GATES_FIX = ("quam_builder.architecture.superconducting.custom_gates."
              "fixed_transmon_pair.two_qubit_gates.")
_GATES_FTC = ("quam_builder.architecture.superconducting.custom_gates."
              "flux_tunable_transmon_pair.two_qubit_gates.")
_QUBIT_FF = "quam_builder.architecture.superconducting.qubit.fixed_frequency_transmon."
_PAIR_FF = ("quam_builder.architecture.superconducting.qubit_pair."
            "fixed_frequency_transmon_pair.FixedFrequencyTransmonPair")
_PULSES = "quam.components.pulses."
_PORT_OUT = "quam.components.ports.analog_outputs.MWFEMAnalogOutputPort"
_PORT_IN = "quam.components.ports.analog_inputs.MWFEMAnalogInputPort"

_NETWORK = {"host": "127.0.0.1", "port": None, "cluster_name": "my_cluster"}

# (qubit, f_01, LO1, LO2) — see the module docstring table.
_B_QUBITS = (("q0", 4.9e9, 5.0e9, 5.0e9),
             ("q1", 5.2e9, 5.3e9, 4.95e9),
             ("q2", 5.0e9, 5.1e9, 4.75e9))
# (pair_id, control, target) — both directions of each physical edge.
_B_PAIRS = (("q0-1", "q0", "q1"), ("q1-0", "q1", "q0"),
            ("q1-2", "q1", "q2"), ("q2-1", "q2", "q1"))
B_EXPECTED_IF = {"q0-1": 200e6, "q1-0": -50e6, "q1-2": 50e6, "q2-1": 450e6}
B_ACTIVE_PAIRS = ["q0-1", "q1-2"]


def _square(length=100, amplitude=1.0, **extra) -> dict:
    d = {"length": length, "amplitude": amplitude, "axis_angle": 0.0,
         "__class__": _PULSES + "SquarePulse"}
    d.update(extra)
    return d


def _flattop(length=300, flat_length=240, amplitude=0.58, **extra) -> dict:
    d = {"length": length, "flat_length": flat_length, "amplitude": amplitude,
         "axis_angle": 0.0, "__class__": _PULSES + "FlatTopGaussianPulse"}
    d.update(extra)
    return d


def _x180(anharmonicity=200e6) -> dict:
    return {"length": 40, "amplitude": 0.5, "axis_angle": 0.0, "alpha": 0.0,
            "anharmonicity": anharmonicity, "detuning": 0,
            "__class__": _PULSES + "DragCosinePulse"}


def _xy_channel(q: str, rf: float, *, upconverter=1) -> dict:
    return {
        "operations": {
            "x180_DragCosine": _x180(),
            "x180": "#./x180_DragCosine",
            "saturation": _square(length=30000, amplitude=0.25),
            "const": _square(length=1000, amplitude=0.5),
        },
        "id": f"{q}.xy",
        "opx_output": f"#/wiring/qubits/{q}/xy/opx_output",
        "upconverter": upconverter,
        "intermediate_frequency": "#./inferred_intermediate_frequency",
        "LO_frequency": "#./upconverter_frequency",
        "RF_frequency": rf,
        "core": f"{q}_con1_slot1",
        "__class__": _COMP + "xy_drive.XYDriveMW",
    }


def _resonator(q: str, f: float) -> dict:
    return {
        "operations": {"readout": {"length": 1000, "amplitude": 0.01,
                                   "__class__": _PULSES + "SquareReadoutPulse"}},
        "id": f"{q}.resonator",
        "opx_output": f"#/wiring/qubits/{q}/rr/opx_output",
        "opx_input": f"#/wiring/qubits/{q}/rr/opx_input",
        "f_01": f, "RF_frequency": f,
        "time_of_flight": 32, "upconverter": 1,
        "__class__": _COMP + "readout_resonator.ReadoutResonatorMW",
    }


def _out_port(*, band=2, upconverter_frequency=None, upconverters=None) -> dict:
    p = {"band": band, "full_scale_power_dbm": 10, "__class__": _PORT_OUT}
    if upconverters is not None:
        p["upconverters"] = upconverters
    else:
        p["upconverter_frequency"] = upconverter_frequency
    return p


def _wiring_qubits(qubits, *, xy_ports) -> dict:
    return {
        q: {
            "rr": {"opx_input": "#/ports/mw_inputs/con1/1/2",
                   "opx_output": "#/ports/mw_outputs/con1/1/1"},
            "xy": {"opx_output": f"#/ports/mw_outputs/con1/1/{xy_ports[q]}"},
        }
        for q in qubits
    }


# ---------------------------------------------------------------------------
# Flavor B — the customer flavor (fa540b6): RF pointer, shared port, directed
# ---------------------------------------------------------------------------

def make_flavor_b(*, sparse: bool = False, with_zz: bool = False,
                  zz_drive_key: bool = True) -> tuple[dict, dict]:
    """(state, wiring) for the ``rf`` flavor. ``sparse=True`` mimics CR_state's
    null-key-stripping serialization; ``with_zz=True`` adds the Stark family
    (``zz_drive_key=False`` would use the tip's ``zz`` key — only meaningful
    with ``with_zz``)."""
    xy_ports = {"q0": 2, "q1": 3, "q2": 4}

    qubits: dict = {}
    for q, f01, _lo1, _lo2 in _B_QUBITS:
        qubits[q] = {
            "id": q, "macros": {}, "f_01": f01,
            "grid_location": f"{xy_ports[q] - 2},0",
            "xy": _xy_channel(q, f01),
            "resonator": _resonator(q, 7.0e9),
            "__class__": _QUBIT_FF + "FixedFrequencyTransmon",
        }

    pairs: dict = {}
    for pid, qc, qt in _B_PAIRS:
        cr = {
            "target_qubit_RF_frequency": f"#/qubits/{qt}/xy/RF_frequency",
            "drive_amplitude_scaling": 1.0,
            "drive_phase": 0.11,
            "cancel_amplitude_scaling": 1.0,
            "cancel_phase": 0.0,
            "qc_correction_phase": 0.0,
            "qt_correction_phase": 0.0,
            "operations": {"square": _square(amplitude=0.79),
                           "flattop": _flattop()},
            "id": f"cr_{qc}_{qt}",
            "intermediate_frequency": "#./inferred_intermediate_frequency",
            "core": f"{qc}_con1_slot1",
            "LO_frequency": f"#/qubits/{qc}/xy/opx_output/upconverters/2/frequency",
            "RF_frequency": "#./inferred_RF_frequency",
            "opx_output": f"#/wiring/qubit_pairs/{pid}/cr/opx_output",
            "upconverter": 2,
            "__class__": _COMP + "cross_resonance.CrossResonanceMW",
        }
        macro = {
            "id": "#./inferred_id", "fidelity": None,
            "duration": "#./inferred_duration",
            "qc_correction_phase": None, "qt_correction_phase": None,
            "__class__": _GATES_FIX + "CRGate",
        }
        if sparse:
            for k in ("drive_amplitude_scaling", "drive_phase",
                      "cancel_amplitude_scaling", "cancel_phase",
                      "qc_correction_phase", "qt_correction_phase"):
                cr.pop(k)
            macro = {"__class__": _GATES_FIX + "CRGate"}
        pair = {
            "id": pid,
            "macros": {"cr": macro},
            "qubit_control": f"#/qubits/{qc}",
            "qubit_target": f"#/qubits/{qt}",
            "cross_resonance": cr,
            "__class__": _PAIR_FF,
        }
        if not sparse:
            pair["zz_drive"] = None
            pair["confusion"] = None
            pair["extras"] = {}
        pairs[pid] = pair

        # Target-side cancellation stubs, pointer-slaved to the pair's CR ops.
        tops = qubits[qt]["xy"]["operations"]
        tops[f"cr_square_{pid}"] = _square(
            amplitude=0.01,
            length=f"#/qubit_pairs/{pid}/cross_resonance/operations/square/length")
        tops[f"cr_flattop_{pid}"] = _flattop(
            amplitude=0.02,
            length=f"#/qubit_pairs/{pid}/cross_resonance/operations/flattop/length",
            flat_length=f"#/qubit_pairs/{pid}/cross_resonance/operations/flattop/flat_length")

    if with_zz:
        zz_key = "zz_drive" if zz_drive_key else "zz"
        for pid, qc, qt in _B_PAIRS[:1]:      # q0-1 carries the Stark family
            pairs[pid][zz_key] = {
                "detuning": -30e6,
                "target_qubit_LO_frequency": 5.3e9,
                "target_qubit_IF_frequency": -100e6,
                "operations": {"square": _square(), "flattop": _flattop()},
                "id": f"zz_{qc}_{qt}",
                "intermediate_frequency": "#./inferred_intermediate_frequency",
                "LO_frequency": f"#/qubits/{qc}/xy/opx_output/upconverters/2/frequency",
                "opx_output": f"#/wiring/qubit_pairs/{pid}/zz/opx_output",
                "upconverter": 2,
                "__class__": _COMP + "zz_drive.ZZDriveMW",
            }
            pairs[pid]["macros"]["stark_cz"] = {
                "qc_correction_phase": 0.0, "qt_correction_phase": 0.0,
                "__class__": _GATES_FIX + "StarkInducedCZGate",
            }
            qubits[qt]["xy_detuned"] = {
                "operations": {f"zz_square_{pid}": _square(amplitude=0.01)},
                "id": f"{qt}.xy_detuned",
                "opx_output": f"#/wiring/qubits/{qt}/xy/opx_output",
                "upconverter": 1,
                "detuning": -30e6,
                "__class__": _COMP + "zz_drive.ZZDriveMW",
            }

    ports_out: dict = {"1": _out_port(upconverter_frequency=7.0e9)}
    for q, _f01, lo1, lo2 in _B_QUBITS:
        ports_out[str(xy_ports[q])] = _out_port(
            upconverters={"1": {"frequency": lo1}, "2": {"frequency": lo2}})

    state = {
        "qubits": qubits,
        "qubit_pairs": pairs,
        "active_qubit_names": [q for q, *_ in _B_QUBITS],
        "active_qubit_pair_names": list(B_ACTIVE_PAIRS),
        "ports": {
            "mw_outputs": {"con1": {"1": ports_out}},
            "mw_inputs": {"con1": {"1": {"2": {
                "band": 2,
                "downconverter_frequency":
                    "#/ports/mw_outputs/con1/1/1/upconverter_frequency",
                "__class__": _PORT_IN,
            }}}},
        },
        "__class__": "quam_config.my_quam.Quam",
    }

    wiring_pairs = {
        pid: {"cr": {
            "control_qubit": f"#/qubits/{qc}",
            "target_qubit": f"#/qubits/{qt}",
            "opx_output": f"#/ports/mw_outputs/con1/1/{xy_ports[qc]}",
        }}
        for pid, qc, qt in _B_PAIRS
    }
    if with_zz:
        zz_key = "zz_drive" if zz_drive_key else "zz"
        for pid, qc, qt in _B_PAIRS[:1]:
            wiring_pairs[pid]["zz"] = {
                "control_qubit": f"#/qubits/{qc}",
                "target_qubit": f"#/qubits/{qt}",
                "opx_output": f"#/ports/mw_outputs/con1/1/{xy_ports[qc]}",
            }
    wiring = {
        "wiring": {
            "qubits": _wiring_qubits([q for q, *_ in _B_QUBITS], xy_ports=xy_ports),
            "qubit_pairs": wiring_pairs,
        },
        "network": dict(_NETWORK),
    }
    return state, wiring


# ---------------------------------------------------------------------------
# Flavor A — a08bf66 (wizard-built): LO/IF literals, dedicated CR ports
# ---------------------------------------------------------------------------

def make_flavor_a() -> tuple[dict, dict]:
    """(state, wiring) for the ``lo_if`` flavor (gen_2x3_cr shape): exhaustive
    nulls, dedicated per-pair CR ports on FEM 2, single direction per edge.
    Pair ``q1-2`` carries a channel ``bell_state_fidelity`` (0.93) and no macro
    fidelity — the channel-fallback test case. IF = 5.2 − 5.0 = +200 MHz."""
    xy_ports = {"q0": 2, "q1": 3, "q2": 4}
    a_pairs = (("q0-1", "q0", "q1", 1), ("q1-2", "q1", "q2", 2))

    qubits = {
        q: {
            "id": q, "macros": {}, "f_01": f01,
            "f_12": None, "anharmonicity": None, "T1": None,
            "T2ramsey": None, "T2echo": None, "chi": None,
            "gate_fidelity": None, "extras": {},
            "grid_location": f"{xy_ports[q] - 2},0",
            "xy": _xy_channel(q, f01),
            "resonator": _resonator(q, 7.0e9),
            "__class__": _QUBIT_FF + "FixedFrequencyTransmon",
        }
        for q, f01, _l1, _l2 in _B_QUBITS
    }

    pairs: dict = {}
    for pid, qc, qt, cr_port in a_pairs:
        pairs[pid] = {
            "id": pid,
            "macros": {"cr": {
                "id": "#./inferred_id", "fidelity": None,
                "duration": "#./inferred_duration",
                "qc_correction_phase": None, "qt_correction_phase": None,
                "__class__": _GATES_FIX + "CRGate",
            }},
            "qubit_control": f"#/qubits/{qc}",
            "qubit_target": f"#/qubits/{qt}",
            "cross_resonance": {
                "target_qubit_LO_frequency": 5.3e9,
                "target_qubit_IF_frequency": -100e6,
                "bell_state_fidelity": 0.93 if pid == "q1-2" else None,
                "drive_amplitude_scaling": 1.0, "drive_phase": 0.0,
                "cancel_amplitude_scaling": 1.0, "cancel_phase": 0.0,
                "qc_correction_phase": 0.0, "qt_correction_phase": 0.0,
                "thread": None, "sticky": None, "digital_outputs": {},
                "operations": {"square": _square(amplitude=0.4),
                               "flattop": _flattop(amplitude=0.4)},
                "id": f"cr_{qc}_{qt}",
                "intermediate_frequency": "#./inferred_intermediate_frequency",
                "core": None,
                "LO_frequency": "#./upconverter_frequency",
                "RF_frequency": "#./inferred_RF_frequency",
                "opx_output": f"#/wiring/qubit_pairs/{pid}/cr/opx_output",
                "upconverter": 1,
                "__class__": _COMP + "cross_resonance.CrossResonanceMW",
            },
            "zz_drive": None,
            "xy_detuned": None,
            "confusion": None,
            "extras": {},
            "__class__": _PAIR_FF,
        }

    ports_out_fem1: dict = {"1": _out_port(upconverter_frequency=7.0e9)}
    for q, _f, lo1, _l2 in _B_QUBITS:
        ports_out_fem1[str(xy_ports[q])] = _out_port(upconverter_frequency=lo1)
    ports_out_fem2 = {str(p): _out_port(upconverter_frequency=5.0e9)
                      for *_x, p in a_pairs}

    state = {
        "octaves": {}, "mixers": {}, "twpas": {},
        "qubits": qubits,
        "qubit_pairs": pairs,
        "active_qubit_names": [q for q, *_ in _B_QUBITS],
        "active_qubit_pair_names": [pid for pid, *_ in a_pairs],
        "active_twpa_names": [],
        "ports": {
            "mw_outputs": {"con1": {"1": ports_out_fem1, "2": ports_out_fem2}},
            "mw_inputs": {"con1": {"1": {"2": {
                "band": 2,
                "downconverter_frequency":
                    "#/ports/mw_outputs/con1/1/1/upconverter_frequency",
                "__class__": _PORT_IN,
            }}}},
        },
        "__class__": ("quam_builder.architecture.superconducting.qpu."
                      "fixed_frequency_quam.FixedFrequencyQuam"),
    }
    wiring = {
        "wiring": {
            "qubits": _wiring_qubits(list(qubits), xy_ports=xy_ports),
            "qubit_pairs": {
                pid: {"cr": {
                    "control_qubit": f"#/qubits/{qc}",
                    "target_qubit": f"#/qubits/{qt}",
                    "opx_output": f"#/ports/mw_outputs/con1/2/{p}",
                }}
                for pid, qc, qt, p in a_pairs
            },
        },
        "network": dict(_NETWORK),
    }
    return state, wiring


# ---------------------------------------------------------------------------
# Flavor C — branch tip (PROVISIONAL, risk R1: no real artifact exists yet)
# ---------------------------------------------------------------------------

def make_flavor_c() -> tuple[dict, dict]:
    """(state, wiring) for the provisional ``rf_drive`` tip flavor:
    ``CrossResonanceDriveMW`` classes, channel keeps only the RF pointer,
    levers live on the CRGate macro, pair ZZ key is ``zz``."""
    state, wiring = make_flavor_b()
    for q in list(state["qubits"]):
        if q == "q2":
            del state["qubits"][q]
    state["active_qubit_names"] = ["q0", "q1"]
    state["active_qubit_pair_names"] = ["q0-1"]
    for pid in list(state["qubit_pairs"]):
        if pid != "q0-1":
            del state["qubit_pairs"][pid]
            del wiring["wiring"]["qubit_pairs"][pid]
    del wiring["wiring"]["qubits"]["q2"]

    pair = state["qubit_pairs"]["q0-1"]
    cr = pair["cross_resonance"]
    for k in ("drive_amplitude_scaling", "drive_phase",
              "cancel_amplitude_scaling", "cancel_phase",
              "qc_correction_phase", "qt_correction_phase"):
        cr.pop(k, None)
    cr["__class__"] = _COMP + "cross_resonance_drive.CrossResonanceDriveMW"
    pair.pop("zz_drive", None)
    pair["zz"] = None
    pair["macros"]["cr"] = {
        "id": "#./inferred_id", "fidelity": None,
        "duration": "#./inferred_duration",
        "drive_amplitude_scaling": 1.0, "drive_phase": 0.0,
        "cancel_amplitude_scaling": 1.0, "cancel_phase": 0.0,
        "qc_correction_phase": 0.0, "qt_correction_phase": 0.0,
        "__class__": _GATES_FIX + "CRGate",
    }
    # Drop q2's cancel stubs from q1 (its pair was removed above).
    q1_ops = state["qubits"]["q1"]["xy"]["operations"]
    for name in list(q1_ops):
        if name.endswith("_q2-1"):
            del q1_ops[name]
    return state, wiring


# ---------------------------------------------------------------------------
# CZ reference — the no-op / regression pin
# ---------------------------------------------------------------------------

def make_cz_reference() -> tuple[dict, dict]:
    """A small flux-tunable CZ chip: z lines, coupler, CZ macro family with a
    ``#./`` alias. ``is_cr_chip`` must be False; every CR accessor must no-op;
    ``get_pair`` output must stay byte-identical across the CR refactor."""
    qubits = {
        q: {
            "id": q, "macros": {}, "f_01": f01,
            "grid_location": f"{i},0",
            "xy": _xy_channel(q, f01),
            "resonator": _resonator(q, 7.0e9),
            "z": {
                "operations": {"const": _square(length=100, amplitude=0.1)},
                "id": f"{q}.z",
                "opx_output": f"#/wiring/qubits/{q}/z/opx_output",
                "joint_offset": 0.02, "flux_point": "joint",
                "__class__": _COMP + "flux_line.FluxLine",
            },
            "__class__": ("quam_builder.architecture.superconducting.qubit."
                          "flux_tunable_transmon.FluxTunableTransmon"),
        }
        for i, (q, f01) in enumerate((("q0", 4.9e9), ("q1", 5.2e9)))
    }
    pairs = {
        "q0-q1": {
            "id": "q0-q1",
            "macros": {
                "cz": "#./cz_unipolar",
                "cz_unipolar": {
                    "flux_pulse_qubit": {"amplitude": 0.1, "length": 48,
                                         "__class__": _PULSES + "SquarePulse"},
                    "phase_shift_control": 0.0,
                    "phase_shift_target": 0.0,
                    "fidelity": None,
                    "__class__": _GATES_FTC + "CZGate",
                },
            },
            "qubit_control": "#/qubits/q0",
            "qubit_target": "#/qubits/q1",
            "coupler": None,
            "detuning": 1.0e6,
            "confusion": None,
            "__class__": ("quam_builder.architecture.superconducting.qubit_pair."
                          "flux_tunable_transmon_pair.FluxTunableTransmonPair"),
        },
    }
    state = {
        "qubits": qubits,
        "qubit_pairs": pairs,
        "ports": {
            "mw_outputs": {"con1": {"1": {
                "1": _out_port(upconverter_frequency=7.0e9),
                "2": _out_port(upconverter_frequency=5.0e9),
                "3": _out_port(upconverter_frequency=5.3e9),
            }}},
            "analog_outputs": {"con1": {"5": {
                "1": {"__class__": "quam.components.ports.analog_outputs."
                                   "LFFEMAnalogOutputPort"},
                "2": {"__class__": "quam.components.ports.analog_outputs."
                                   "LFFEMAnalogOutputPort"},
            }}},
        },
        "__class__": ("quam_builder.architecture.superconducting.qpu."
                      "flux_tunable_quam.FluxTunableQuam"),
    }
    wiring = {
        "wiring": {
            "qubits": {
                q: {
                    "rr": {"opx_input": "#/ports/mw_inputs/con1/1/2",
                           "opx_output": "#/ports/mw_outputs/con1/1/1"},
                    "xy": {"opx_output": f"#/ports/mw_outputs/con1/1/{i + 2}"},
                    "z": {"opx_output": f"#/ports/analog_outputs/con1/5/{i + 1}"},
                }
                for i, q in enumerate(qubits)
            },
        },
        "network": dict(_NETWORK),
    }
    return state, wiring


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def write_folder(folder: Path, state: dict, wiring: dict) -> Path:
    """Write ``state.json`` + ``wiring.json`` into *folder* (created if needed);
    returns *folder* for chaining into ``QuamStore(folder)`` / app fixtures."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state, indent=2))
    (folder / "wiring.json").write_text(json.dumps(wiring, indent=2))
    return folder


# A synthetic 1×5 port-label CSV in the customer's exact column layout
# (see /generate/import-port-csv). Scrubbed values only.
PORT_LABEL_CSV_1X5 = """\
Chip qubit index ,chip mux ,mux row ,mux column ,chip port                ,chip control row within mux ,chip control col within mux ,chip control row ,chip control column ,QM chassis ,QM FEM ,QM port
,,,,,,,,,,,
,0,0,0,readout to chip mux 0    ,,,,,1,1,1
,,,,,,,,,,,
,0,0,0,readout from chip mux 0  ,,,,,1,1,IN2
,,,,,,,,,,,
0,0,0,0,control Q0              ,0,0,0,0,1,1,2
,,,,,,,,,,,
1,0,0,0,control Q1              ,0,1,0,1,1,1,3
,,,,,,,,,,,
2,0,0,0,control Q2              ,0,2,0,2,1,1,4
,,,,,,,,,,,
3,0,0,0,control Q3              ,0,3,0,3,1,1,5
,,,,,,,,,,,
4,0,0,0,control Q4              ,0,4,0,4,1,1,6
"""
