"""Lab additions, phase 4: presets (readout per quam_builder version, pulsed
TWPA as pulse + fields), the ``field`` addition kind, the both-movers CZ group,
and QDAC qubits (a z-line pulse refused where the bias replaced ``z``).

Real builds are skipped unless their interpreter is given:

* ``QSM_OFFICIAL_PYTHON`` (else a conda env named ``qsm_official``),
* ``QSM_LAB_TREE_PYTHON`` + ``QSM_LAB_TREE_PYTHONPATH`` -- an env that imports a
  COPY of a lab source tree placed first on ``PYTHONPATH`` (the both-movers
  gate class lives only in such a tree),
* ``QSM_QDAC_PYTHON`` -- an env with the QDAC bias classes.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from quam_state_manager.core import additions, config_generator, role_manifest

QB = "quam_builder.architecture.superconducting."
BOTH = "lab.gates.CZGateBothMovers"
FLAT = "quam.components.pulses.FlatTopGaussianPulse"


def _f(raw, has_default=False, kind="plain", optional=False):
    return {"raw": raw, "has_default": has_default, "kind": kind, "optional": optional}


MAN = {"versions": {"quam_builder": "0.4.0"}, "classes": {
    BOTH: {"role": "macro_pair", "origin": "lab", "fields": {
        "flux_pulse_high": _f("Union[Pulse, str]", kind="pointer_pulse"),
        "flux_pulse_low": _f("Union[Pulse, str]", kind="pointer_pulse"),
        "coupler_flux_pulse": _f("Pulse", True, "pointer_pulse", True)}},
    FLAT: {"role": "pulse", "origin": "official", "fields": {
        "length": _f("int"), "amplitude": _f("float"), "flat_length": _f("int")}},
}}


def _state(f1=5.2e9, f2=4.9e9):
    q = lambda f: {"f_01": f, "z": {"operations": {}}, "xy": {"operations": {}}}
    return {"__class__": "x.Root", "qubits": {"q1": q(f1), "q2": q(f2)},
            "qubit_pairs": {"q1-2": {"qubit_control": "#/qubits/q1",
                                     "qubit_target": "#/qubits/q2"}}}


def _spec(adds):
    return {"qubits": ["q1", "q2"], "qubit_pairs": [["q1", "q2"]],
            "lines": [{"element": q, "line": ln} for q in ("q1", "q2")
                      for ln in ("drive", "flux", "resonator")],
            "additions": adds}


def _pulse(q, name):
    return {"kind": "pulse", "target": f"qubits.{q}.z.operations", "name": name, "class": FLAT,
            "fields": {"length": 40, "amplitude": 0.1, "flat_length": 24}}


def _both(high, low):
    return {"kind": "macro", "target": "qubit_pairs.q1-2.macros", "name": "cz_both", "class": BOTH,
            "fields": {"flux_pulse_high": f"#/qubits/{high}/z/operations/p_{high}",
                       "flux_pulse_low": f"#/qubits/{low}/z/operations/p_{low}"}}


def _run(adds, state):
    rep = additions.apply_additions(state, _spec(adds), MAN)
    return [e["name"] for e in rep["added"]], {e["name"]: e["reason"] for e in rep["refused"]}


# ---------------------------------------------------------------------------
# both movers: the high pulse sits on the higher-frequency qubit
# ---------------------------------------------------------------------------

def test_both_movers_high_pulse_on_the_higher_qubit_is_accepted():
    added, _ = _run([_pulse("q1", "p_q1"), _pulse("q2", "p_q2"), _both("q1", "q2")], _state())
    assert "cz_both" in added


def test_both_movers_high_pulse_on_the_lower_qubit_is_refused():
    added, by = _run([_pulse("q1", "p_q1"), _pulse("q2", "p_q2"), _both("q2", "q1")], _state())
    assert "cz_both" not in added
    assert "higher-frequency qubit" in by["cz_both"], by


def test_both_movers_with_unknown_frequencies_leaves_either_qubit_open():
    st = _state(None, None)
    added, _ = _run([_pulse("q1", "p_q1"), _pulse("q2", "p_q2"), _both("q2", "q1")], st)
    assert "cz_both" in added


def test_both_movers_tie_goes_to_the_control():
    added, by = _run([_pulse("q1", "p_q1"), _pulse("q2", "p_q2"), _both("q2", "q1")],
                     _state(5e9, 5e9))
    assert "cz_both" not in added and "higher-frequency" in by["cz_both"]


# ---------------------------------------------------------------------------
# real build: the both-movers gate of a lab source tree (a COPY on PYTHONPATH)
# ---------------------------------------------------------------------------

def _lab_tree_env(monkeypatch):
    py = os.environ.get("QSM_LAB_TREE_PYTHON")
    tree = os.environ.get("QSM_LAB_TREE_PYTHONPATH")
    if not (py and tree and Path(py).is_file() and Path(tree).is_dir()):
        pytest.skip("QSM_LAB_TREE_PYTHON / QSM_LAB_TREE_PYTHONPATH not given")
    monkeypatch.setenv("PYTHONPATH", tree)
    return py


def test_real_build_lab_both_movers_group(tmp_path, monkeypatch):
    from tests.test_lab_additions_p3 import _plan_rows
    from tests.test_lab_additions_p2 import _real_spec
    py = _lab_tree_env(monkeypatch)
    module = os.environ.get("QSM_LAB_TREE_BOTH_MOVERS_MODULE", "quam_config.cz_gate_both_movers")
    gate = module + ".CZGateBothMovers"
    man = role_manifest.probe_roles(py, None, state_classes=[gate], modules=[module])
    assert man["ok"], man["error"]
    entry = man["manifest"]["classes"][gate]
    assert entry["role"] == "macro_pair"
    recipe = additions.cz_recipe(entry)
    assert [r["on"] for r in recipe[:2]] == ["high", "low"], recipe

    out = tmp_path / "chip"
    spec = _real_spec()
    o = config_generator.run_generator(py, "build", spec, out, timeout=900)
    assert o.get("ok"), o
    built = json.loads((out / "state.json").read_text(encoding="utf-8"))
    pair = built["qubit_pairs"]["q1-2"]
    f = {q: built["qubits"][q].get("f_01") for q in ("q1", "q2")}
    high, low = ("q1", "q2") if f["q1"] >= f["q2"] else ("q2", "q1")
    flat = {"length": 48, "amplitude": 0.08, "flat_length": 32}
    spec["additions"] = [
        {"kind": "pulse", "target": f"qubits.{high}.z.operations", "name": "cz_hi", "class": FLAT,
         "fields": dict(flat)},
        {"kind": "pulse", "target": f"qubits.{low}.z.operations", "name": "cz_lo", "class": FLAT,
         "fields": dict(flat, amplitude=-0.05)},
        {"kind": "macro", "target": "qubit_pairs.q1-2.macros", "name": "cz_both", "class": gate,
         "fields": {"flux_pulse_high": f"#/qubits/{high}/z/operations/cz_hi",
                    "flux_pulse_low": f"#/qubits/{low}/z/operations/cz_lo"}},
        # fault: the pulses swapped (the high pulse on the lower-frequency qubit)
        {"kind": "macro", "target": "qubit_pairs.q1-2.macros", "name": "cz_both_swapped",
         "class": gate,
         "fields": {"flux_pulse_high": f"#/qubits/{low}/z/operations/cz_lo",
                    "flux_pulse_low": f"#/qubits/{high}/z/operations/cz_hi"}},
    ]
    rep = additions.apply_and_verify(py, out, spec, man["manifest"])
    (tmp_path / "additions_report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    added = [e["name"] for e in rep["added"]]
    by = {e["name"]: e["reason"] for e in rep["refused"]}
    assert added == ["cz_hi", "cz_lo", "cz_both"], by
    assert "higher-frequency qubit" in by["cz_both_swapped"]
    assert "qubit_pairs.q1-2.macros.cz_both" in rep["verified"]["own_code"]
    prev = config_generator.run_config_preview(py, out)
    assert prev.get("ok"), prev
    st = json.loads((out / "state.json").read_text(encoding="utf-8"))
    assert st["qubit_pairs"]["q1-2"]["macros"]["cz_both"]["__class__"] == gate
    html = _plan_rows(tmp_path, out)
    (tmp_path / "cz_plan_both.html").write_text(html, encoding="utf-8")
    assert "qubit_pairs.q1-2.macros.cz_both" in html
    from quam_state_manager.core import cz_gate_plan as czp
    row = next(r for r in czp.operations(st)[0] if r["path"].endswith("macros.cz_both"))
    assert {ln["pulse_field"] for ln in row["lines"]} == {"flux_pulse_high", "flux_pulse_low"}
    assert pair  # the builder wrote the pair


# ---------------------------------------------------------------------------
# the field kind
# ---------------------------------------------------------------------------

ROOT = "lab.root.Quam"
SQ = "quam.components.pulses.SquarePulse"
FMAN = {"versions": {"quam_builder": "0.4.0"}, "classes": {
    ROOT: {"role": "root", "origin": "lab", "fields": {
        "extras": _f("dict", True), "active_twpa_names": _f("List[str]", True),
        "twpas": _f("dict", True)}},
    SQ: {"role": "pulse", "origin": "official", "fields": {
        "length": _f("int"), "amplitude": _f("float")}},
}}


def _twpa_state(extras=True):
    st = {"__class__": ROOT, "twpas": {"t1": {
        "__class__": "x.TWPA",
        "pump_": {"__class__": "x.Ch", "operations": {
            "pump": {"__class__": SQ, "length": 2000, "amplitude": 0.5}}}}}}
    if extras:
        st["extras"] = {}
    return st


def _field(target, value):
    return {"kind": "field", "target": target, "value": value}


def _frun(adds, state, man=FMAN):
    rep = additions.apply_additions(state, {"qubits": [], "additions": adds}, man)
    return rep, {f"{e['target']}.{e['name']}".lstrip("."): e["reason"] for e in rep["refused"]}


def test_structural_field_needs_a_value_and_a_dot_path():
    errs = additions.structural_errors({"additions": [{"kind": "field", "target": "a b"}]})
    assert any("dot path" in e for e in errs) and any("needs a value" in e for e in errs)
    assert not additions.structural_errors({"additions": [_field("extras.x", 1)]})
    errs = additions.structural_errors({"additions": [_field("extras.x", 1), _field("extras.x", 2)]})
    assert any("set twice" in e for e in errs)


def test_extras_is_free_form_and_created_when_absent():
    st = _twpa_state(extras=False)
    rep, by = _frun([_field("extras.twpa_pulse_mode", True), _field("extras.any.nested", [1])], st)
    assert not by, by
    assert st["extras"] == {"twpa_pulse_mode": True, "any": {"nested": [1]}}


def test_a_declared_root_field_is_set_and_typed():
    st = _twpa_state()
    rep, by = _frun([_field("active_twpa_names", ["t1"])], st)
    assert not by and st["active_twpa_names"] == ["t1"]
    rep, by = _frun([_field("active_twpa_names", "t1")], _twpa_state())
    assert "does not fit its type" in by["active_twpa_names"]


def test_an_undeclared_root_field_is_refused():
    man = copy.deepcopy(FMAN)
    del man["classes"][ROOT]["fields"]["active_twpa_names"]
    st = _twpa_state()
    rep, by = _frun([_field("active_twpa_names", [])], st, man)
    assert "is not a field of Quam" in by["active_twpa_names"]
    assert "active_twpa_names" not in st


def test_extras_needs_a_root_that_declares_extras():
    man = copy.deepcopy(FMAN)
    del man["classes"][ROOT]["fields"]["extras"]
    rep, by = _frun([_field("extras.twpa_pulse_mode", True)], _twpa_state(False), man)
    assert "extras is not a field" in by["extras.twpa_pulse_mode"]


def test_a_field_of_a_pulse_object_is_set_by_its_own_class():
    st = _twpa_state()
    rep, by = _frun([_field("twpas.t1.pump_.operations.pump.length", 20000)], st)
    assert not by and st["twpas"]["t1"]["pump_"]["operations"]["pump"]["length"] == 20000
    rep, by = _frun([_field("twpas.t1.pump_.operations.pump.sticky", 1)], _twpa_state())
    assert "not a field of SquarePulse" in by["twpas.t1.pump_.operations.pump.sticky"]


def test_a_field_addition_never_replaces_an_object_or_writes_into_a_container():
    rep, by = _frun([_field("twpas.t1.pump_.operations.pump", 1)], _twpa_state())
    assert "not an object with fields" in next(iter(by.values()))
    rep, by = _frun([_field("twpas.t1.pump_.operations.newop", 1)], _twpa_state())
    assert "not an object with fields" in next(iter(by.values()))


def test_extras_that_is_not_a_block_is_never_overwritten():
    st = _twpa_state()
    st["extras"] = "a label"
    rep, by = _frun([_field("extras.twpa_pulse_mode", True)], st)
    assert "is not a block" in by["extras.twpa_pulse_mode"] and st["extras"] == "a label"


def test_a_twpa_given_as_an_id_object_is_in_the_chip():
    spec = {"twpas": [{"id": "twpa1"}], "qubits": [], "lines": []}
    assert additions.spec_channel_reason(spec, "twpas.twpa1.pump_.operations") is None
    assert "not in this chip" in additions.spec_channel_reason(spec, "twpas.twpa2.pump_.operations")


def test_a_field_addition_never_replaces_a_whole_object():
    rep, by = _frun([_field("twpas.t1.pump_", 1)], _twpa_state())
    assert "holds an object" in by["twpas.t1.pump_"]


def test_an_unknown_class_is_left_to_the_env_load():
    rep, by = _frun([_field("twpas.t1.settling_time", 20)], _twpa_state())
    assert not by     # x.TWPA is not in the manifest: the env load decides


def test_fields_are_placed_after_pulses_and_macros():
    adds = [_field("extras.a", 1), {"kind": "pulse", "target": "x.y.operations", "name": "p"},
            {"kind": "macro", "target": "qubits.q.macros", "name": "m"}]
    assert additions.placement_order(adds) == [1, 2, 0]


def test_precheck_judges_a_root_field_when_the_root_is_named():
    man = copy.deepcopy(FMAN)
    del man["classes"][ROOT]["fields"]["active_twpa_names"]
    spec = {"quam_class": ROOT, "qubits": [],
            "additions": [_field("active_twpa_names", []), _field("extras.f", True),
                          _field("twpas.t1.settling_time", 3)]}
    items = additions.precheck(spec, man)
    assert "is not a field" in items[0]["reason"]
    assert not items[1].get("reason") and not items[2].get("reason")
    spec.pop("quam_class")
    assert not any(e.get("reason") for e in additions.precheck(spec, man))


def test_a_refused_field_in_the_env_round_restores_the_old_value(tmp_path):
    st = _twpa_state()
    (tmp_path / "state.json").write_text(json.dumps(st), encoding="utf-8")
    (tmp_path / "wiring.json").write_text("{}", encoding="utf-8")
    spec = {"qubits": [], "additions": [_field("twpas.t1.pump_.operations.pump.length", 7),
                                        _field("extras.ok", True)]}

    def verify(py, folders, timeout=0):
        out = {}
        for k, f in folders.items():
            s = json.loads((Path(f["folder"]) / "state.json").read_text(encoding="utf-8"))
            bad = s["twpas"]["t1"]["pump_"]["operations"]["pump"]["length"] == 7
            out[k] = {"ok": not bad, "error": "length 7 refused" if bad else None}
        return {"ok": True, "folders": out}
    rep = additions.apply_and_verify("py", tmp_path, spec, FMAN, verify=verify)
    assert [e["name"] for e in rep["added"]] == ["ok"]
    assert "length 7 refused" in rep["refused"][0]["reason"]
    final = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert final["twpas"]["t1"]["pump_"]["operations"]["pump"]["length"] == 2000
    assert final["extras"]["ok"] is True
    assert all("undo" not in e for e in rep["added"] + rep["refused"])


# ---------------------------------------------------------------------------
# readout pulses with array fields; QDAC qubits
# ---------------------------------------------------------------------------

CW = "lab.readout.ComplexWeightsReadoutPulse"
RMAN = {"versions": {}, "classes": {CW: {"role": "pulse", "origin": "lab", "fields": {
    "length": _f("<class 'int'>"), "amplitude": _f("<class 'float'>"),
    "integration_weights": _f("typing.Union[typing.List[float], typing.List[typing.Tuple[float, int]]]", True),
    "weights_real": _f("List[float]", True), "u_centers": _f("List[List[float]]", True)}}}}


def _ro(**fields):
    return {"kind": "pulse", "target": "qubits.q1.resonator.operations", "name": "readout_cw",
            "class": CW, "fields": dict({"length": 400, "amplitude": 0.1}, **fields)}


def test_readout_array_fields_take_plain_lists():
    st = {"qubits": {"q1": {"resonator": {"operations": {}}}}}
    rep = additions.apply_additions(st, _spec([_ro(weights_real=[0.1, 0.2], u_centers=[[1.0, 2.0]],
                                                   integration_weights=[[1.0, 400]])]), RMAN)
    assert [e["name"] for e in rep["added"]] == ["readout_cw"], rep["refused"]
    assert st["qubits"]["q1"]["resonator"]["operations"]["readout_cw"]["weights_real"] == [0.1, 0.2]


def test_readout_array_field_refuses_a_scalar():
    st = {"qubits": {"q1": {"resonator": {"operations": {}}}}}
    rep = additions.apply_additions(st, _spec([_ro(weights_real=3)]), RMAN)
    assert "does not fit its type" in rep["refused"][0]["reason"]
    rep = additions.apply_additions(copy.deepcopy(st), _spec([_ro(integration_weights=3)]), RMAN)
    assert "does not fit its type" in rep["refused"][0]["reason"]


def _qdac_spec(tee=False):
    s = _spec([])
    s["lines"] = [ln for ln in s["lines"] if not (ln["element"] == "q1" and ln["line"] == "flux")]
    s["qdac"] = {"qubits": {"q1": {"channel": 1, "bias_tee": tee}}}
    if tee:
        s["lines"].append({"element": "q1", "line": "flux"})
    return s


def test_qdac_qubit_z_pulse_is_refused_before_the_build():
    r = additions.spec_channel_reason(_qdac_spec(), "qubits.q1.z.operations")
    assert "QDAC-II" in r and "plays no pulses" in r
    assert additions.spec_channel_reason(_qdac_spec(), "qubits.q1.xy.operations") is None
    assert additions.spec_channel_reason(_qdac_spec(), "qubits.q1.resonator.operations") is None
    assert additions.spec_channel_reason(_qdac_spec(), "qubits.q2.z.operations") is None
    assert additions.spec_channel_reason(_qdac_spec(tee=True), "qubits.q1.z.operations") is None


def test_qdac_qubit_z_pulse_is_refused_on_the_built_chip():
    st = {"qubits": {"q1": {"z": {"__class__": "lab.qdac.QdacBiasLine", "channel": 1},
                            "xy": {"operations": {}}}}}
    spec = _spec([{"kind": "pulse", "target": "qubits.q1.z.operations", "name": "p", "class": FLAT,
                   "fields": {"length": 40, "amplitude": 0.1, "flat_length": 24}}])
    spec["qubits"] = ["q1"]
    rep = additions.apply_additions(st, spec, MAN)
    assert "QdacBiasLine" in rep["refused"][0]["reason"] and "plays no pulses" in rep["refused"][0]["reason"]


# ---------------------------------------------------------------------------
# the roles route carries what the presets need; the wizard (jsdom)
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    from quam_state_manager.web.app import create_app
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    return app.test_client()


def test_roles_route_carries_readout_and_root_capabilities(client, monkeypatch):
    man = {"versions": {"quam_builder": "0.4.0"}, "classes": {
        CW: {"role": "pulse", "origin": "lab", "fields": {},
             "mro": ["quam.components.pulses.SquareReadoutPulse", "quam.components.pulses.ReadoutPulse"]},
        SQ: {"role": "pulse", "origin": "official", "fields": {}, "mro": ["quam.components.pulses.Pulse"]},
        ROOT: {"role": "root", "origin": "lab", "fields": {"extras": {}, "active_twpa_names": {}}}}}
    monkeypatch.setattr(config_generator, "get_selected_env", lambda _p: "py")
    monkeypatch.setattr(role_manifest, "roles_for_env", lambda *a, **k: man)
    body = client.get("/generate/roles").get_json()
    assert body["pulses"][CW]["readout"] is True and body["pulses"][SQ]["readout"] is False
    assert body["readout"]["mode"] == "pulse_only" and "0.4.0" in body["readout"]["reason"]
    assert body["roots"][ROOT] == {"origin": "lab", "active_twpa_names": True, "extras": True}
    assert body["measure_macro"].endswith(".MeasureMacro")


def test_wizard_selfcheck():
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("node not installed")
    p = subprocess.run(["node", str(Path(__file__).with_name("gen_additions_p4_selfcheck.cjs"))],
                       capture_output=True, text=True, encoding="utf-8")
    if p.returncode == 2 and "jsdom not installed" in p.stderr:
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, p.stdout + p.stderr
    assert " 0 failed" in p.stdout


# ---------------------------------------------------------------------------
# real builds: readout preset per quam_builder version
# ---------------------------------------------------------------------------

def _build(tmp_path, py, spec=None):
    from tests.test_lab_additions_p2 import _real_spec
    out = tmp_path / "chip"
    spec = spec or _real_spec()
    o = config_generator.run_generator(py, "build", spec, out, timeout=900)
    assert o.get("ok"), o
    return out, spec


def _verify_and_preview(tmp_path, py, out, spec, modules=()):
    classes = sorted({a["class"] for a in spec["additions"] if a.get("class")})
    man = role_manifest.probe_roles(py, None, state_classes=classes, modules=list(modules))["manifest"]
    rep = additions.apply_and_verify(py, out, spec, man)
    (tmp_path / "additions_report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    prev = config_generator.run_config_preview(py, out)
    return man, rep, prev, json.loads((out / "state.json").read_text(encoding="utf-8"))


def test_real_build_readout_preset_with_measure_macro(tmp_path):
    from tests.test_lab_additions_p2 import _env
    py = _env("QSM_OFFICIAL_PYTHON", "qsm_official")
    if not py:
        pytest.skip("environment absent")
    out, spec = _build(tmp_path, py)
    sro = "quam.components.pulses.SquareReadoutPulse"
    spec["additions"] = [
        {"kind": "pulse", "target": "qubits.q1.resonator.operations", "name": "readout_sq", "class": sro,
         "fields": {"length": 1200, "amplitude": 0.05}},
        {"kind": "macro", "target": "qubits.q1.macros", "name": "measure", "class": role_manifest._MEASURE_MACRO,
         "fields": {"pulse": "#/qubits/q1/resonator/operations/readout_sq"}},
        # fault: the macro points at a readout pulse that does not exist
        {"kind": "macro", "target": "qubits.q1.macros", "name": "measure_bad", "class": role_manifest._MEASURE_MACRO,
         "fields": {"pulse": "#/qubits/q1/resonator/operations/no_such"}},
    ]
    man, rep, prev, st = _verify_and_preview(tmp_path, py, out, spec)
    assert role_manifest.readout_capability(man)["mode"] == "pulse_and_macro"
    assert [e["name"] for e in rep["added"]] == ["readout_sq", "measure"], rep["refused"]
    assert "points at nothing" in rep["refused"][0]["reason"]
    assert prev.get("ok"), prev
    assert st["qubits"]["q1"]["macros"]["measure"]["pulse"] == "#/qubits/q1/resonator/operations/readout_sq"


def test_real_build_lab_readout_pulse_with_weights(tmp_path):
    from tests.test_lab_additions_p2 import _env
    py = _env("QSM_LAB_ENV_PYTHON")
    if not py:
        pytest.skip("QSM_LAB_ENV_PYTHON not given")
    mod = os.environ.get("QSM_LAB_READOUT_MODULE", "quam_config.complex_weights_pulse")
    cls = mod + "." + os.environ.get("QSM_LAB_READOUT_CLASS", "ComplexWeightsReadoutPulse")
    out, spec = _build(tmp_path, py)
    n = 400
    w_re = [round(0.5 + 0.001 * i, 6) for i in range(n)]
    w_im = [round(-0.25 + 0.0005 * i, 6) for i in range(n)]
    spec["additions"] = [
        {"kind": "pulse", "target": "qubits.q1.resonator.operations", "name": "readout_cw", "class": cls,
         "fields": {"length": n, "amplitude": 0.05, "weights_real": w_re, "weights_imag": w_im}},
        # fault: a scalar where the class declares a list
        {"kind": "pulse", "target": "qubits.q2.resonator.operations", "name": "readout_cw", "class": cls,
         "fields": {"length": n, "amplitude": 0.05, "weights_real": 1.0}},
        {"kind": "macro", "target": "qubits.q1.macros", "name": "measure", "class": role_manifest._MEASURE_MACRO,
         "fields": {"pulse": "#/qubits/q1/resonator/operations/readout_cw"}},
    ]
    man, rep, prev, st = _verify_and_preview(tmp_path, py, out, spec, modules=[mod])
    cap = role_manifest.readout_capability(man)
    by = {(e["target"], e["name"]): e["reason"] for e in rep["refused"]}
    assert [e["name"] for e in rep["added"]] == ["readout_cw"], rep["refused"]
    assert "does not fit its type" in by[("qubits.q2.resonator.operations", "readout_cw")]
    if cap["mode"] == "pulse_only":
        assert "not in this environment" in by[("qubits.q1.macros", "measure")]
    assert "qubits.q1.resonator.operations.readout_cw" in rep["verified"]["own_code"]
    assert prev.get("ok"), prev
    assert st["qubits"]["q1"]["resonator"]["operations"]["readout_cw"]["weights_real"] == w_re


# ---------------------------------------------------------------------------
# real builds: pulsed TWPA preset (fields only)
# ---------------------------------------------------------------------------

def _twpa_spec():
    from tests.test_lab_additions_p2 import _real_spec
    spec = _real_spec()
    spec["twpas"] = [{"id": "twpa1"}]
    spec["lines"] = spec["lines"] + [{"element": "twpa1", "line": "twpa_pump", "channel": None}]
    spec["instruments"]["controllers"][0]["fems"].append({"slot": 3, "fem": "mw"})
    return spec


def _preset(length=20000, amplitude=None, active=("twpa1",)):
    out = [_field("extras.twpa_pulse_mode", True),
           _field("twpas.twpa1.pump_.operations.pump.length", length)]
    if amplitude is not None:
        out.append(_field("twpas.twpa1.pump_.operations.pump.amplitude", amplitude))
    if active is not None:
        out.append(_field("active_twpa_names", list(active)))
    return out


@pytest.mark.parametrize("var, default", [("QSM_OFFICIAL_PYTHON", "qsm_official"), ("QSM_LAB_ENV_PYTHON", None)])
def test_real_build_pulsed_twpa_preset(tmp_path, var, default):
    from tests.test_lab_additions_p2 import _env
    py = _env(var, default)
    if not py:
        pytest.skip(f"{var} not given")
    out, spec = _build(tmp_path, py, _twpa_spec())
    spec["additions"] = _preset() + [
        # faults: an undeclared root field, a field inside a container, a wrong type
        _field("twpa_pulse_mode", True),
        _field("twpas.twpa1.pump_.operations.pump2", 1),
        _field("twpas.twpa1.pump_.operations.pump.amplitude", "loud"),
    ]
    man, rep, prev, st = _verify_and_preview(tmp_path, py, out, spec)
    roots = role_manifest.root_capabilities(man)
    declared = roots[st["__class__"]]["active_twpa_names"]
    added = {e["path"] for e in rep["added"]}
    by = {(e["target"] + "." if e["target"] else "") + e["name"]: e["reason"] for e in rep["refused"]}
    assert {"extras.twpa_pulse_mode", "twpas.twpa1.pump_.operations.pump.length"} <= added, by
    assert ("active_twpa_names" in added) == declared, (declared, by)
    if not declared:
        assert "is not a field of" in by["active_twpa_names"]
    assert "is not a field of" in by["twpa_pulse_mode"]
    assert "not an object with fields" in by["twpas.twpa1.pump_.operations.pump2"]
    assert "does not fit its type" in by["twpas.twpa1.pump_.operations.pump.amplitude"]
    assert prev.get("ok"), prev
    assert st["extras"]["twpa_pulse_mode"] is True
    assert st["twpas"]["twpa1"]["pump_"]["operations"]["pump"]["length"] == 20000


def test_pulsed_twpa_round_trip_on_a_copy_of_a_real_state(tmp_path):
    """A copy of a real pulsed-TWPA state, its pulsed parts taken back to what
    the builder writes, then the preset applied: the TWPA, the flag and the
    active list come back equal to the source, and the lab env loads it."""
    from tests.test_lab_additions_p2 import _env
    py = _env("QSM_LAB_ENV_PYTHON")
    src = os.environ.get("QSM_PULSED_TWPA_STATE")
    if not (py and src and (Path(src) / "state.json").is_file()):
        pytest.skip("QSM_LAB_ENV_PYTHON / QSM_PULSED_TWPA_STATE not given")
    import shutil
    out = tmp_path / "chip"
    out.mkdir()
    for n in ("state.json", "wiring.json"):
        shutil.copyfile(Path(src) / n, out / n)
    source = json.loads((out / "state.json").read_text(encoding="utf-8"))
    t = next(iter(source["twpas"]))
    pump = source["twpas"][t]["pump_"]["operations"]["pump"]
    stripped = copy.deepcopy(source)
    stripped["extras"].pop("twpa_pulse_mode", None)
    sp = stripped["twpas"][t]["pump_"]["operations"]["pump"]
    sp["length"], sp["amplitude"] = 2000, 0.5          # the builder's pump_ pulse
    stripped["active_twpa_names"] = ["placeholder"]
    (out / "state.json").write_text(json.dumps(stripped, indent=4), encoding="utf-8")
    spec = {"qubits": sorted(source["qubits"]), "twpas": [t],
            "additions": [_field("extras.twpa_pulse_mode", source["extras"]["twpa_pulse_mode"]),
                          _field(f"twpas.{t}.pump_.operations.pump.length", pump["length"]),
                          _field(f"twpas.{t}.pump_.operations.pump.amplitude", pump["amplitude"]),
                          _field("active_twpa_names", source["active_twpa_names"])]}
    man = role_manifest.probe_roles(py, None, state_path=str(out / "state.json"))["manifest"]
    rep = additions.apply_and_verify(py, out, spec, man)
    (tmp_path / "additions_report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    assert len(rep["added"]) == 4, rep["refused"]
    back = json.loads((out / "state.json").read_text(encoding="utf-8"))
    assert back["twpas"] == source["twpas"]
    assert back["extras"] == source["extras"]
    assert back["active_twpa_names"] == source["active_twpa_names"]
    assert back == source
    prev = config_generator.run_config_preview(py, out)
    assert prev.get("ok"), prev


# ---------------------------------------------------------------------------
# real build: a QDAC chip (the bias replaces z on a QDAC-only qubit)
# ---------------------------------------------------------------------------

def test_real_build_qdac_chip_refuses_z_pulses_on_the_qdac_qubit(tmp_path):
    from tests.test_generate_qdac_live_build import _qdac_test_spec
    from tests.test_lab_additions_p2 import _env
    py = _env("QSM_QDAC_PYTHON")
    if not py:
        pytest.skip("QSM_QDAC_PYTHON not given")
    from quam_state_manager.core import capabilities
    spec = _qdac_test_spec()
    probe = config_generator.probe_capabilities(py, None)
    root = capabilities.qpu_root_check(spec, {"capabilities": probe.get("capabilities"),
                                              "versions": probe.get("versions"),
                                              "qpu_roots": probe.get("qpu_roots")})
    assert not root["blocker"], root
    spec["quam_class"] = root["chosen"]          # the build route's own root pick
    out, spec = _build(tmp_path, py, spec)
    built = json.loads((out / "state.json").read_text(encoding="utf-8"))
    assert "QdacBias" in built["qubits"]["q1"]["z"]["__class__"]

    def pulse(target, name):
        return {"kind": "pulse", "target": target, "name": name, "class": FLAT,
                "fields": {"length": 48, "amplitude": 0.05, "flat_length": 32}}
    spec["additions"] = [pulse("qubits.q1.z.operations", "z_on_qdac"),
                         pulse("qubits.q1.xy.operations", "xy_flat"),
                         pulse("qubits.q1.resonator.operations", "ro_flat"),
                         pulse("qubits.q2.z.operations", "z_flat")]
    pre = {e["name"]: e.get("reason") for e in additions.precheck(spec, None)}
    assert "QDAC-II" in pre["z_on_qdac"] and pre["xy_flat"] is None
    # the built-chip check says it too when the spec does not name the QDAC qubit
    nospec = copy.deepcopy(spec)
    nospec.pop("qdac")
    nospec["lines"].append({"element": "q1", "line": "flux"})
    st = copy.deepcopy(built)
    r = additions.apply_additions(st, {**nospec, "additions": spec["additions"][:1]}, None)
    assert "QdacBiasLine" in r["refused"][0]["reason"]
    man, rep, prev, st = _verify_and_preview(tmp_path, py, out, spec)
    assert [e["name"] for e in rep["added"]] == ["xy_flat", "ro_flat", "z_flat"], rep["refused"]
    assert "plays no pulses" in rep["refused"][0]["reason"]
    assert prev.get("ok"), prev
    assert "QdacBias" in st["qubits"]["q1"]["z"]["__class__"] and "operations" not in st["qubits"]["q1"]["z"]
