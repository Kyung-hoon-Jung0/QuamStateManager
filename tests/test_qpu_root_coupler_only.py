"""A coupler-only CZ chip with QDAC qubits is judged against a fixed-frequency root.

The build refuses any non-FixedFrequencyQuam root for the coupler-only CZ. The
review used to pick the first QDAC-capable root (a flux-tunable lab root), say
nothing, and the build then failed with a bare RuntimeError.
"""
from quam_state_manager.core import capabilities

FLUX_ROOT = {"path": "lab.my_quam.Quam", "importable": True, "holds_qdac": True, "fixed_root": False}
FIXED_ROOT = {"path": "lab.fixed.FixedQuam", "importable": True, "holds_qdac": True, "fixed_root": True}


def _spec(gate):
    return {"qubits": ["q1"], "qubit_pairs": [], "lines": [], "pair_gate": gate,
            "qdac": {"qubits": {"q1": {"channel": 1}}}}


def test_only_flux_roots_hold_qdac_refuses_coupler_only_chip():
    out = capabilities.qpu_root_check(_spec("cz_coupler"), {"qpu_roots": [FLUX_ROOT]})
    assert out["blocker"] and "fixed-frequency" in out["blocker"], out
    assert out["chosen"] is None


def test_a_fixed_root_is_chosen_over_a_flux_one():
    out = capabilities.qpu_root_check(_spec("cz_coupler"), {"qpu_roots": [FLUX_ROOT, FIXED_ROOT]})
    assert out["blocker"] is None and out["chosen"] == FIXED_ROOT["path"], out


def test_declared_flux_root_is_refused_for_coupler_only():
    spec = _spec("cz_coupler")
    spec["quam_class"] = FLUX_ROOT["path"]
    out = capabilities.qpu_root_check(spec, {"qpu_roots": [FLUX_ROOT]})
    assert out["blocker"] and "fixed-frequency" in out["blocker"]


def test_other_gates_still_take_the_flux_root():
    out = capabilities.qpu_root_check(_spec("cz_tunable"), {"qpu_roots": [FLUX_ROOT]})
    assert out["blocker"] is None and out["chosen"] == FLUX_ROOT["path"]


def test_an_unprobed_flag_is_not_a_negative():
    root = {k: v for k, v in FLUX_ROOT.items() if k != "fixed_root"}
    out = capabilities.qpu_root_check(_spec("cz_coupler"), {"qpu_roots": [root]})
    assert out["blocker"] is None
