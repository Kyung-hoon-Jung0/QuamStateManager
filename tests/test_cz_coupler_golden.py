"""The QSM-extension coupler-only CZ (`cz_coupler`) stays byte-for-byte (decisions C-1, T-1 pure layer).

The goldens were captured on main 667c956c before the lab-layout mode
(`cz_coupler_only`) was added. Everything a `cz_coupler` spec flows through
without the QM stack is compared: the emitted recipe files, validation,
required capabilities, the capability review, the root check, the gate names,
and the spec reconstructed from a stored chip of that layout.

Re-capture only on purpose: ``python tests/test_cz_coupler_golden.py --capture``.
"""
import hashlib
import json
import sys
from pathlib import Path

from quam_state_manager.core import additions, capabilities, config_generator, regen_spec, script_emitter

GOLDEN = Path(__file__).parent / "golden" / "cz_coupler_ext_layout.json"


def _spec():
    lines = []
    for q in ("q1", "q2"):
        lines += [{"element": q, "line": "drive"}, {"element": q, "line": "resonator"}]
    lines.append({"element": "q1-2", "line": "coupler"})
    return {"qubits": ["q1", "q2"], "qubit_pairs": [["q1", "q2"]], "lines": lines,
            "pair_gate": "cz_coupler", "network": {}, "instruments": {},
            "populate": {"pairs": {"q1-2": {"cz_interaction_duration": 120, "coupler_amplitude": 0.2,
                                            "phase_shift_control": 0.1, "phase_shift_target": 0.2}}}}


def _manifest():
    caps = {cid: {"available": True} for cid in capabilities.REGISTRY}
    roots = [{"path": "lab.flux.Quam", "importable": True, "holds_qdac": True, "fixed_root": False},
             {"path": "lab.fixed.Quam", "importable": True, "holds_qdac": True, "fixed_root": True}]
    return {"capabilities": caps, "qpu_roots": roots}


def _ext_state():
    pulse = "cz_coupler_pulse_q1_q2"
    return {
        "__class__": "quam_builder.architecture.superconducting.qpu.FixedFrequencyQuam",
        "qubits": {q: {"id": q, "__class__": "quam_builder.architecture.superconducting.qubit."
                       "fixed_frequency_transmon.FixedFrequencyTransmon"} for q in ("q1", "q2")},
        "qubit_pairs": {"q1-2": {
            "__class__": "qsm_quam_ext.FixedFrequencyTunableCouplerPair",
            "id": "q1-2", "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
            "coupler": {"id": "coupler_q1_q2", "decouple_offset": 0.01, "interaction_offset": 0.02,
                        "operations": {pulse: {"__class__": "quam_builder.common.pulses.FlatTopGaussianPulse",
                                               "amplitude": 0.2, "length": 120, "flat_length": 100,
                                               "id": pulse}}},
            "macros": {"cz_coupler": {"__class__": "qsm_quam_ext.CouplerCZGate",
                                      "coupler_flux_pulse": "#../../coupler/operations/" + pulse,
                                      "phase_shift_control": 0.1, "phase_shift_target": 0.2},
                       "cz": "#./cz_coupler"}}},
    }


def _norm(obj):
    """JSON-stable form: sets become sorted lists."""
    if isinstance(obj, (set, frozenset)):
        return sorted(_norm(x) for x in obj)
    if isinstance(obj, dict):
        return {str(k): _norm(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_norm(x) for x in obj]
    return obj


def observe():
    spec = _spec()
    bundle = script_emitter.emit_bundle(spec, {}, {}, chip_name="chip", stamp="2026-01-01")
    rec = regen_spec.reconstruct_spec(_ext_state(), {"wiring": {}})
    rspec = getattr(rec, "spec", rec)
    return _norm({
        "bundle_sha256": {k: hashlib.sha256(v.encode("utf-8")).hexdigest() for k, v in sorted(bundle.items())},
        "validate": config_generator.validate_spec(spec),
        "required": capabilities.required_capabilities(spec),
        "assess": capabilities.assess(spec, _manifest()),
        "root_check": capabilities.qpu_root_check(spec, _manifest()),
        "gate_names": additions.builder_gate_names(spec, ["q1", "q2"]),
        "reconstructed": {"pair_gate": rspec.get("pair_gate"), "quam_class": rspec.get("quam_class"),
                          "pairs": ((rspec.get("populate") or {}).get("pairs") or {})},
    })


def test_the_ext_layout_is_unchanged():
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    got = observe()
    for key in want:
        assert got[key] == want[key], key


if __name__ == "__main__" and "--capture" in sys.argv:
    GOLDEN.write_text(json.dumps(observe(), indent=1, sort_keys=True), encoding="utf-8")
    print("captured", GOLDEN)
