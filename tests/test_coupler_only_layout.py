"""The lab layout of the coupler-only CZ (`cz_coupler_only`): pure checks (decisions T-2).

Fixed-frequency qubits with no flux line, a tunable coupler per pair, the lab's
own CouplerCZGate under `cz_coupler_only`. Everything here runs without the QM
stack; the real build is the live test (T-5).
"""
from quam_state_manager.core import capabilities as cap
from quam_state_manager.core import config_generator, coupler_cz_layout as ccl
from quam_state_manager.generator.probe_capabilities import CATALOG

LAB_ROOT = {"path": "quam_config.my_quam.Quam", "importable": True, "holds_qdac": False, "fixed_root": False}
STOCK_FLUX = {"path": "quam_builder.architecture.superconducting.qpu.FluxTunableQuam",
              "importable": True, "holds_qdac": False, "fixed_root": False}
STOCK_FIXED = {"path": "quam_builder.architecture.superconducting.qpu.FixedFrequencyQuam",
               "importable": True, "holds_qdac": False, "fixed_root": True}


def _spec(shape=None, extra_lines=()):
    lines = []
    for q in ("q1", "q2"):
        lines += [{"element": q, "line": "drive"}, {"element": q, "line": "resonator"}]
    lines.append({"element": "q1-2", "line": "coupler"})
    lines += list(extra_lines)
    pairs = {"q1-2": {"coupler_pulse_shape": shape}} if shape else {}
    return {"qubits": ["q1", "q2"], "qubit_pairs": [["q1", "q2"]], "lines": lines,
            "pair_gate": "cz_coupler_only", "network": {}, "instruments": {},
            "populate": {"pairs": pairs}}


def _manifest(available=None, roots=(LAB_ROOT, STOCK_FLUX, STOCK_FIXED)):
    caps = {cid: {"available": True} for cid in cap.REGISTRY}
    for cid, ok in (available or {}).items():
        caps[cid] = {"available": ok}
    return {"capabilities": caps, "qpu_roots": list(roots)}


# --- names ---------------------------------------------------------------------

def test_names_match_the_lab_helper():
    assert ccl.MACRO_KEY == "cz_coupler_only" and ccl.ALIAS_KEY == "cz"
    assert ccl.pulse_key("q1", "q2") == "cz_coupler_only_pulse_q1_q2"
    assert ccl.flattop_length(60) == 76          # ceil((60 + 2*8)/4)*4
    assert ccl.flattop_length(61) == 80


def test_the_probe_and_the_registry_name_the_lab_classes():
    assert CATALOG["pair.coupler_cz_lab"] == {"module": ccl.LAB_GATE_MODULE, "attr": ccl.LAB_GATE_CLASS}
    assert CATALOG["pulse.snz_two_flux"] == {"module": ccl.LAB_SNZ_MODULE, "attr": ccl.LAB_SNZ_CLASS}
    assert cap.REGISTRY["pair.coupler_cz_lab"]["severity"] == cap.BLOCKER


# --- validation (D-12) -----------------------------------------------------------

def test_the_mode_validates_without_qubit_flux_lines():
    errs = config_generator.validate_spec(_spec())
    assert not [e for e in errs if "pair_gate" in e or "coupler lines need qubit flux" in e], errs


def test_a_qubit_flux_line_is_refused():
    errs = config_generator.validate_spec(_spec(extra_lines=[{"element": "q1", "line": "flux"}]))
    assert any("cz_coupler_only requires no qubit z line" in e for e in errs), errs


def test_an_unknown_pulse_shape_is_refused():
    errs = config_generator.validate_spec(_spec(shape="gauss"))
    assert any("coupler_pulse_shape" in e for e in errs), errs


# --- capabilities (D-6) ------------------------------------------------------------

def test_required_capabilities_follow_the_shapes():
    req = cap.required_capabilities(_spec())
    assert {"pair.coupler_cz_lab", "pulse.cr_flattop", "qpu.flux_tunable"} <= req
    assert "pair.coupler_cz" not in req and "pair.cz_gate" not in req and "pulse.snz_two_flux" not in req
    req = cap.required_capabilities(_spec(shape="snz"))
    assert "pulse.snz_two_flux" in req and "pulse.cr_flattop" not in req


def test_the_lab_gate_missing_blocks_the_build():
    out = cap.assess(_spec(), _manifest({"pair.coupler_cz_lab": False}))
    assert not out["buildable"]
    assert [b["id"] for b in out["blockers"]] == ["pair.coupler_cz_lab"]


def test_an_snz_pair_needs_the_snz_class():
    out = cap.assess(_spec(shape="snz"), _manifest({"pulse.snz_two_flux": False}))
    assert [b["id"] for b in out["blockers"]] == ["pulse.snz_two_flux"]


def test_the_extension_gate_is_not_needed():
    out = cap.assess(_spec(), _manifest({"pair.coupler_cz": False}))
    assert out["buildable"], out["blockers"]


# --- root (D-3, D-7, Q-6) ------------------------------------------------------------

def test_the_lab_root_is_chosen_first():
    out = cap.qpu_root_check(_spec(), _manifest(roots=(STOCK_FLUX, STOCK_FIXED, LAB_ROOT)))
    assert out["chosen"] == LAB_ROOT["path"] and out["blocker"] is None and out["warning"] is None


def test_a_stock_flux_root_builds_with_a_warning():
    out = cap.qpu_root_check(_spec(), _manifest(roots=(STOCK_FLUX, STOCK_FIXED)))
    assert out["chosen"] == STOCK_FLUX["path"] and out["blocker"] is None
    assert "initialize_qpu" in out["warning"]


def test_a_declared_fixed_root_is_refused():
    spec = _spec()
    spec["quam_class"] = STOCK_FIXED["path"]
    out = cap.qpu_root_check(spec, _manifest())
    assert out["blocker"] and "fixed-frequency" in out["blocker"]


def test_a_declared_root_that_does_not_import_is_refused():
    spec = _spec()
    spec["quam_class"] = "quam_config.my_quam.Quam"
    broken = dict(LAB_ROOT, importable=False)
    out = cap.qpu_root_check(spec, _manifest(roots=(broken,)))
    assert out["blocker"] and "does not import" in out["blocker"]


def test_a_declared_root_the_probe_never_saw_is_allowed():
    spec = _spec()
    spec["quam_class"] = "somewhere.else.Quam"
    out = cap.qpu_root_check(spec, _manifest())
    assert out["blocker"] is None and out["chosen"] == "somewhere.else.Quam"


def test_no_probe_is_not_a_negative():
    out = cap.qpu_root_check(_spec(), {})
    assert out["blocker"] is None and out["chosen"] is None
