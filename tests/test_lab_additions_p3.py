"""Lab additions, phase 3: macro additions, the CZ group, and the P2 UX fixes.

Covers the macro spec slot, the role gate (a qubit macro never goes on a
pair), placement order (pulses before macros), the pulse references a macro
holds (pointer lands on a pulse of the right qubit / coupler, a plain name
exists exactly once there, a reference to a refused addition says so), the
builder's pair id, the pre-build check the Review step lists, the env-verify
fallback with macros, the CZ plan reading an added macro, the wizard under
jsdom, and real builds in the official env and a lab env (skipped when absent).
"""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import additions, config_generator

QB = "quam_builder.architecture.superconducting."
CZ = QB + "custom_gates.flux_tunable_transmon_pair.two_qubit_gates.CZGate"
TWO = "lab.gates.CZGateTwoFlux"
MEAS = QB + "custom_gates.single_qubit_gates.MeasureMacro"
BYNAME = "lab.macros.PlayByName"
FLAT = "quam.components.pulses.FlatTopGaussianPulse"


def _f(raw, has_default=False, kind="plain", default_repr=None, optional=False):
    return {"raw": raw, "has_default": has_default, "kind": kind,
            "default_repr": default_repr, "optional": optional}


_CZ_FIELDS = {
    "flux_pulse_qubit": _f("Union[Pulse, str]", kind="pointer_pulse"),
    "coupler_flux_pulse": _f("Pulse", True, kind="pointer_pulse", default_repr="None", optional=True),
    "spectator_qubits_control": _f("dict[str, Pulse]", True, kind="dict_of_pointers", default_repr="<factory>"),
    "phase_shift_control": _f("float", True, default_repr="0.0"),
    "duration_qubit": _f("ScalarInt", True, kind="scalar_or_pointer", default_repr="None", optional=True),
}
MAN = {"versions": {"quam_builder": "0.6.0"}, "classes": {
    FLAT: {"role": "pulse", "origin": "official", "abstract": False, "fields": {
        "length": _f("int"), "amplitude": _f("float"), "flat_length": _f("int")}},
    CZ: {"role": "macro_pair", "origin": "official", "abstract": False, "fields": dict(_CZ_FIELDS)},
    TWO: {"role": "macro_pair", "origin": "lab", "abstract": False, "fields": dict(
        _CZ_FIELDS, flux_pulse_target=_f("Union[Pulse, str, None]", True, kind="pointer_pulse",
                                         default_repr="None", optional=True))},
    MEAS: {"role": "macro_qubit", "origin": "official", "abstract": False, "fields": {
        "pulse": _f("Union[ReadoutPulse, str]", True, kind="pointer_pulse", default_repr="'readout'")}},
    BYNAME: {"role": "macro_qubit", "origin": "lab", "abstract": False, "fields": {
        "pulse_name": _f("str")}},
    "lab.gates.Both": {"role": "macro_pair", "origin": "lab", "abstract": False, "fields": {
        "flux_pulse_high": _f("Pulse", kind="pointer_pulse"), "flux_pulse_low": _f("Pulse", kind="pointer_pulse")}},
}}


def _spec(adds=None):
    s = {"qubits": ["q1", "q2", "q3"], "qubit_pairs": [["q1", "q2"]],
         "lines": [{"element": q, "line": ln} for q in ("q1", "q2", "q3") for ln in ("drive", "flux", "resonator")]
         + [{"element": "q1-q2", "line": "coupler"}]}
    if adds is not None:
        s["additions"] = adds
    return s


def _state(moving="control"):
    q = lambda: {"xy": {"operations": {"x180": {"__class__": "p.P", "length": 40}}},
                 "z": {"operations": {"const": {"__class__": "p.P"}}},
                 "resonator": {"operations": {"readout": {"__class__": "p.P"}}}}
    return {"__class__": "x.Root",
            "qubits": {"q1": q(), "q2": q(), "q3": q()},
            "qubit_pairs": {"q1-2": {"qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
                                     "moving_qubit": moving,
                                     "coupler": {"operations": {"c0": {"__class__": "p.P"}}},
                                     "macros": {"cz_unipolar": {"__class__": CZ}}}}}


def _pulse(q="q1", name="cz_p", ch="z"):
    return {"kind": "pulse", "target": f"qubits.{q}.{ch}.operations", "name": name, "class": FLAT,
            "fields": {"length": 40, "amplitude": 0.1, "flat_length": 24}}


def _cz(name="cz_added", cls=CZ, pair="q1-2", **fields):
    return {"kind": "macro", "target": f"qubit_pairs.{pair}.macros", "name": name, "class": cls,
            "fields": fields}


def _run(adds, state=None, man=MAN):
    st = state or _state()
    rep = additions.apply_additions(st, _spec(adds), man)
    return rep, st, {e["name"]: e["reason"] for e in rep["refused"]}


# ---------------------------------------------------------------------------
# spec shape
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target, ok", [
    ("qubit_pairs.q1-2.macros", True), ("qubits.q1.macros", True),
    ("qubit_pairs.q1-2.operations", False), ("qubits.q1.z.macros", False), ("macros", False),
])
def test_macro_target_shape(target, ok):
    errs = additions.structural_errors({"additions": [
        {"kind": "macro", "target": target, "name": "m", "class": "a.B"}]})
    assert (errs == []) is ok, errs


def test_pulse_target_still_must_be_operations():
    errs = additions.structural_errors({"additions": [
        {"kind": "pulse", "target": "qubits.q1.macros", "name": "m", "class": "a.B"}]})
    assert any("operations dict" in e for e in errs)


# ---------------------------------------------------------------------------
# the pair id is the builder's own
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pair", [["q1", "q2"], ["q10", "q11"], ["qA1", "qB2"], "q3-q4", ["Q1", "Q2"]])
def test_pair_id_is_run_build_s_own(pair):
    from quam_state_manager.core.script_emitter import _run_build
    long = pair if isinstance(pair, str) else f"{pair[0]}-{pair[1]}"
    assert additions.quam_pair_id(pair) == _run_build()._quam_pair_id(long)


def test_a_long_pair_id_is_refused_with_the_builder_s_id():
    _, _, by = _run([_cz(pair="q1-q2", flux_pulse_qubit="const")])
    assert "named q1-2 in the built chip" in by["cz_added"]


# ---------------------------------------------------------------------------
# role gate
# ---------------------------------------------------------------------------

def test_qubit_macro_class_on_a_pair_is_refused():
    _, _, by = _run([_cz(cls=MEAS)])
    assert "is a qubit macro (role macro_qubit), so it cannot go on a pair" in by["cz_added"]


def test_pair_macro_class_on_a_qubit_is_refused():
    _, _, by = _run([{"kind": "macro", "target": "qubits.q1.macros", "name": "m", "class": CZ,
                      "fields": {"flux_pulse_qubit": "const"}}])
    assert "is a pair macro (role macro_pair), so it cannot go on a qubit" in by["m"]


def test_pulse_class_as_macro_is_refused():
    _, _, by = _run([_cz(cls=FLAT)])
    assert "is a pulse" in by["cz_added"]


def test_macro_class_as_pulse_is_refused():
    _, _, by = _run([dict(_pulse(), **{"class": CZ})])
    assert "not a pulse" in by["cz_p"]


# ---------------------------------------------------------------------------
# ordering and pointers
# ---------------------------------------------------------------------------

def test_pulses_are_placed_before_macros():
    adds = [_cz(flux_pulse_qubit="#/qubits/q1/z/operations/cz_p"), _pulse()]
    assert additions.placement_order(adds) == [1, 0]
    rep, st, by = _run(adds)
    assert not by, by
    assert [e["name"] for e in rep["added"]] == ["cz_added", "cz_p"]   # reported in spec order
    assert st["qubit_pairs"]["q1-2"]["macros"]["cz_added"]["flux_pulse_qubit"] == \
        "#/qubits/q1/z/operations/cz_p"


def test_macro_pointing_at_a_refused_pulse_says_so():
    bad = dict(_pulse(), fields={"length": 40})      # required fields missing
    _, st, by = _run([bad, _cz(flux_pulse_qubit="#/qubits/q1/z/operations/cz_p")])
    assert "required fields missing" in by["cz_p"]
    assert "qubits.q1.z.operations.cz_p, an addition that was refused" in by["cz_added"]
    assert "cz_added" not in st["qubit_pairs"]["q1-2"]["macros"]


def test_pointer_must_land_on_a_pulse():
    _, _, by = _run([_cz(flux_pulse_qubit="#/qubits/q1/z")])
    assert "does not point at a pulse" in by["cz_added"]


def test_single_mover_pulse_must_be_on_the_moving_qubit():
    _, _, by = _run([_pulse("q2"), _cz(flux_pulse_qubit="#/qubits/q2/z/operations/cz_p")])
    assert "but this gate plays flux_pulse_qubit on q1 (the pair's moving qubit, control)" in by["cz_added"]
    rep, _, by = _run([_pulse("q2"), _cz(flux_pulse_qubit="#/qubits/q2/z/operations/cz_p")],
                      state=_state(moving="target"))
    assert not by, by


def test_two_flux_target_pulse_must_be_on_the_target():
    adds = [_pulse("q1", "a"), _pulse("q2", "b"),
            _cz(cls=TWO, flux_pulse_qubit="#/qubits/q1/z/operations/a",
                flux_pulse_target="#/qubits/q1/z/operations/a")]
    _, _, by = _run(adds)
    assert "plays flux_pulse_target on q2 (the pair's target)" in by["cz_added"]
    adds[2]["fields"]["flux_pulse_target"] = "#/qubits/q2/z/operations/b"
    rep, st, by = _run(adds)
    assert not by and len(rep["added"]) == 3


def test_two_flux_control_pulse_is_the_control_even_when_the_target_moves():
    adds = [_pulse("q2", "b"), _cz(cls=TWO, flux_pulse_qubit="#/qubits/q2/z/operations/b")]
    _, _, by = _run(adds, state=_state(moving="target"))
    assert "plays flux_pulse_qubit on q1 (the pair's control)" in by["cz_added"]


def test_coupler_pulse_must_be_on_the_coupler():
    _, _, by = _run([_cz(flux_pulse_qubit="const", coupler_flux_pulse="#/qubits/q1/z/operations/const")])
    assert "plays coupler_flux_pulse on the pair's coupler" in by["cz_added"]
    rep, _, by = _run([_cz(flux_pulse_qubit="const",
                           coupler_flux_pulse="#/qubit_pairs/q1-2/coupler/operations/c0")])
    assert not by, by


def test_spectator_dict_values_are_checked():
    _, _, by = _run([_cz(flux_pulse_qubit="const",
                         spectator_qubits_control={"q3": "#/qubits/q3/z/operations/nope"})])
    assert "spectator_qubits_control.q3" in by["cz_added"] and "points at nothing" in by["cz_added"]


# ---------------------------------------------------------------------------
# a pulse played by NAME
# ---------------------------------------------------------------------------

def test_a_name_that_exists_on_the_moving_qubit_is_accepted():
    rep, _, by = _run([_cz(flux_pulse_qubit="const")])
    assert not by and rep["added"]


def test_a_missing_name_is_refused():
    _, _, by = _run([_cz(flux_pulse_qubit="cz_nope")])
    assert "plays an operation named 'cz_nope', which does not exist on q1" in by["cz_added"]


def test_a_name_on_the_other_qubit_is_refused():
    _, _, by = _run([_pulse("q2", "only_q2"), _cz(flux_pulse_qubit="only_q2")])
    assert "does not exist on q1" in by["cz_added"]


def test_a_name_of_a_refused_pulse_says_so():
    bad = dict(_pulse("q1", "cz_x"), fields={"length": 40})
    _, _, by = _run([bad, _cz(flux_pulse_qubit="cz_x")])
    assert "plays 'cz_x', an addition on q1" in by["cz_added"] and "refused" in by["cz_added"]


def test_an_ambiguous_name_is_refused():
    _, _, by = _run([_pulse("q1", "dup", "z"), _pulse("q1", "dup", "xy"), _cz(flux_pulse_qubit="dup")])
    assert "is not unique on q1" in by["cz_added"]


def test_a_qubit_macro_str_field_naming_an_operation_is_checked():
    add = {"kind": "macro", "target": "qubits.q1.macros", "name": "m", "class": BYNAME,
           "fields": {"pulse_name": "x180"}}
    rep, st, by = _run([add])
    assert not by and st["qubits"]["q1"]["macros"]["m"]["pulse_name"] == "x180"   # macros dict created
    add["fields"]["pulse_name"] = "x999"
    _, _, by = _run([add])
    assert "named 'x999', which does not exist on q1" in by["m"]


def test_no_manifest_still_checks_known_cz_fields_by_name():
    _, _, by = _run([_cz(flux_pulse_qubit="cz_nope")], man=None)
    assert "does not exist on q1" in by["cz_added"]


def test_macro_name_taken():
    _, _, by = _run([_cz(name="cz_unipolar", flux_pulse_qubit="const")])
    assert "already has a macro named cz_unipolar" in by["cz_unipolar"]


# ---------------------------------------------------------------------------
# the CZ recipe and the pre-build list
# ---------------------------------------------------------------------------

def test_cz_recipe_reads_the_class_fields():
    r = additions.cz_recipe(MAN["classes"][CZ])
    assert [(i["field"], i["on"], i["required"]) for i in r] == [
        ("flux_pulse_qubit", "moving", True), ("coupler_flux_pulse", "coupler", False)]
    r = additions.cz_recipe(MAN["classes"][TWO])
    assert [(i["field"], i["on"]) for i in r][:2] == [("flux_pulse_qubit", "control"),
                                                      ("flux_pulse_target", "target")]
    r = additions.cz_recipe(MAN["classes"]["lab.gates.Both"])
    assert [i["on"] for i in r] == ["high", "low"]
    assert additions.cz_recipe(MAN["classes"][MEAS]) is None
    assert additions.cz_recipe(MAN["classes"][FLAT]) is None


def test_precheck_lists_in_placement_order_with_known_refusals():
    items = additions.precheck(_spec([_cz(cls=MEAS), _pulse(), _pulse("q9")]), MAN)
    assert [e["name"] for e in items] == ["cz_p", "cz_p", "cz_added"]
    assert items[0].get("reason") is None
    assert "qubit q9 is not in this chip" in items[1]["reason"]
    assert "qubit macro" in items[2]["reason"]


@pytest.fixture
def client(tmp_path):
    from quam_state_manager.web.app import create_app
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    return app.test_client()


def test_capabilities_route_carries_the_additions(client, monkeypatch):
    from quam_state_manager.core import role_manifest
    monkeypatch.setattr(config_generator, "get_selected_env", lambda _p: "py")
    monkeypatch.setattr(config_generator, "probe_capabilities", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(role_manifest, "roles_for_env", lambda *a, **k: MAN)
    body = client.post("/generate/capabilities", json={"spec": _spec([_pulse(), _cz(cls=MEAS)])}).get_json()
    adds = body["additions"]
    assert adds["manifest"] is True and [e["kind"] for e in adds["items"]] == ["pulse", "macro"]
    assert "qubit macro" in adds["items"][1]["reason"]
    body = client.post("/generate/capabilities", json={"spec": _spec()}).get_json()
    assert body["additions"] is None


def test_roles_route_lists_macros_with_their_cz_recipe(client, monkeypatch):
    from quam_state_manager.core import role_manifest
    monkeypatch.setattr(config_generator, "get_selected_env", lambda _p: "py")
    monkeypatch.setattr(role_manifest, "roles_for_env", lambda *a, **k: MAN)
    body = client.get("/generate/roles").get_json()
    assert set(body["macros_pair"]) == {CZ, TWO, "lab.gates.Both"}
    assert set(body["macros_qubit"]) == {MEAS, BYNAME}
    assert body["macros_pair"][TWO]["cz"][1]["field"] == "flux_pulse_target"
    assert body["macros_qubit"][MEAS]["cz"] is None


# ---------------------------------------------------------------------------
# env verification with macros (fake env)
# ---------------------------------------------------------------------------

def _built(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    (d / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (d / "wiring.json").write_text("{}", encoding="utf-8")
    return d


def test_env_refusing_a_pulse_refuses_the_macro_that_plays_it(tmp_path):
    out = _built(tmp_path)
    calls = []

    def verify(python, folders, timeout=600):
        calls.append({k: v["own_code"] for k, v in folders.items()})
        res = {}
        for k, item in folders.items():
            st = json.loads(Path(item["folder"], "state.json").read_text(encoding="utf-8"))
            bad = "bad" in st["qubits"]["q1"]["z"]["operations"]
            # like the real macro check: a macro whose pulse is not in THIS folder fails
            for p in item["own_code"]:
                if ".macros." in p:
                    m = st["qubit_pairs"]["q1-2"]["macros"][p.rsplit(".", 1)[-1]]
                    name = m["flux_pulse_qubit"].rsplit("/", 1)[-1]
                    bad = bad or name not in st["qubits"]["q1"]["z"]["operations"]
            res[k] = {"ok": not bad, "error": "generate_config: bad pulse" if bad else None}
        return {"ok": True, "folders": res}
    adds = [_pulse("q1", "good"), _pulse("q1", "bad"),
            _cz("cz_good", flux_pulse_qubit="#/qubits/q1/z/operations/good"),
            _cz("cz_bad", flux_pulse_qubit="#/qubits/q1/z/operations/bad")]
    rep = additions.apply_and_verify("py", out, _spec(adds), MAN, verify=verify)
    assert [e["name"] for e in rep["added"]] == ["good", "cz_good"], rep
    by = {e["name"]: e["reason"] for e in rep["refused"]}
    assert "environment refused it" in by["bad"]
    assert "an addition that was refused" in by["cz_bad"]
    # the macro round grafts the kept pulse with it, and a macro runs its own code
    assert any("qubit_pairs.q1-2.macros.cz_good" in v for c in calls for v in c.values())
    st = json.loads((out / "state.json").read_text(encoding="utf-8"))
    assert "cz_good" in st["qubit_pairs"]["q1-2"]["macros"] and "bad" not in st["qubits"]["q1"]["z"]["operations"]
    assert rep["verified"]["own_code"] == ["qubit_pairs.q1-2.macros.cz_good"]


# ---------------------------------------------------------------------------
# the CZ plan reads an added macro, incl. the second pulse of a two-flux gate
# ---------------------------------------------------------------------------

def _plan_doc():
    st = _state()
    for q, f in (("q1", 5.2e9), ("q2", 4.9e9)):
        st["qubits"][q].update({"f_01": f, "anharmonicity": -2e8})
    adds = [_pulse("q1", "a"), _pulse("q2", "b"),
            _cz("cz_two", cls=TWO, flux_pulse_qubit="#/qubits/q1/z/operations/a",
                flux_pulse_target="#/qubits/q2/z/operations/b")]
    rep = additions.apply_additions(st, _spec(adds), MAN)
    assert not rep["refused"], rep
    return st


def test_cz_plan_reads_an_added_two_flux_macro():
    from quam_state_manager.core import cz_gate_plan as czp
    rows, _conv = czp.operations(_plan_doc())
    row = next(r for r in rows if r["path"].endswith("macros.cz_two"))
    assert row["kind"] == "two-flux"
    by_field = {ln["pulse_field"]: ln for ln in row["lines"]}
    assert set(by_field) == {"flux_pulse_qubit", "flux_pulse_target"}
    assert by_field["flux_pulse_target"]["pulse_path"].startswith("qubits.q2.z.operations.b")


# ---------------------------------------------------------------------------
# the wizard (jsdom)
# ---------------------------------------------------------------------------

def test_wizard_selfcheck():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    p = subprocess.run(["node", str(Path(__file__).with_name("gen_additions_p3_selfcheck.cjs"))],
                       capture_output=True, text=True, encoding="utf-8")
    if p.returncode == 2 and "jsdom not installed" in p.stderr:
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "0 failed" in p.stdout


# ---------------------------------------------------------------------------
# real builds
# ---------------------------------------------------------------------------

def _real(tmp_path, py, adds_for, modules=()):
    """Build the official 2Q spec in *py*, then add ``adds_for(state)``."""
    from quam_state_manager.core import role_manifest
    from tests.test_lab_additions_p2 import _real_spec
    if not py:
        pytest.skip("environment absent")
    out = tmp_path / "chip"
    spec = _real_spec()
    o = config_generator.run_generator(py, "build", spec, out, timeout=600)
    assert o.get("ok"), o
    built = json.loads((out / "state.json").read_text(encoding="utf-8"))
    spec["additions"] = adds_for(built)
    classes = sorted({a["class"] for a in spec["additions"]})
    man = role_manifest.probe_roles(py, None, state_classes=classes, modules=list(modules))["manifest"]
    rep = additions.apply_and_verify(py, out, spec, man)
    (tmp_path / "additions_report.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    prev = config_generator.run_config_preview(py, out)
    return out, rep, prev, json.loads((out / "state.json").read_text(encoding="utf-8"))


def _plan_rows(tmp_path, folder):
    from tests.test_cz_plan_page import _client
    c = _client(tmp_path, folder=folder)
    return c.get("/cz-plan").get_data(as_text=True)


def test_real_build_official_cz_gate(tmp_path):
    from tests.test_lab_additions_p2 import _env

    def adds_for(st):
        pair = st["qubit_pairs"]["q1-2"]
        mv = "q1" if pair.get("moving_qubit", "control") == "control" else "q2"
        other = "q2" if mv == "q1" else "q1"
        flat = {"length": 48, "amplitude": 0.08, "flat_length": 32}
        return [
            {"kind": "macro", "target": "qubit_pairs.q1-2.macros", "name": "cz_flat", "class": CZ,
             "fields": {"flux_pulse_qubit": f"#/qubits/{mv}/z/operations/cz_flat_pulse"}},
            {"kind": "pulse", "target": f"qubits.{mv}.z.operations", "name": "cz_flat_pulse",
             "class": FLAT, "fields": flat},
            # faults: a macro pointing at a refused pulse, a qubit macro on a pair,
            # a name that does not exist on the moving qubit, the pulse on the wrong qubit
            {"kind": "pulse", "target": f"qubits.{mv}.z.operations", "name": "cz_broken",
             "class": FLAT, "fields": {"length": 48}},
            {"kind": "macro", "target": "qubit_pairs.q1-2.macros", "name": "cz_on_broken", "class": CZ,
             "fields": {"flux_pulse_qubit": f"#/qubits/{mv}/z/operations/cz_broken"}},
            {"kind": "macro", "target": "qubit_pairs.q1-2.macros", "name": "measure_on_pair", "class": MEAS,
             "fields": {}},
            {"kind": "macro", "target": "qubit_pairs.q1-2.macros", "name": "cz_by_missing_name",
             "class": CZ, "fields": {"flux_pulse_qubit": "cz_no_such_op"}},
            {"kind": "pulse", "target": f"qubits.{other}.z.operations", "name": "cz_other",
             "class": FLAT, "fields": flat},
            {"kind": "macro", "target": "qubit_pairs.q1-2.macros", "name": "cz_wrong_qubit", "class": CZ,
             "fields": {"flux_pulse_qubit": f"#/qubits/{other}/z/operations/cz_other"}},
        ]
    out, rep, prev, st = _real(tmp_path, _env("QSM_OFFICIAL_PYTHON", "qsm_official"), adds_for)
    added = [e["name"] for e in rep["added"]]
    assert added == ["cz_flat", "cz_flat_pulse", "cz_other"], [(e["name"], e["reason"]) for e in rep["refused"]]
    by = {e["name"]: e["reason"] for e in rep["refused"]}
    assert "required fields missing" in by["cz_broken"]
    assert "an addition that was refused" in by["cz_on_broken"]
    assert "qubit macro" in by["measure_on_pair"]
    assert "does not exist on" in by["cz_by_missing_name"]
    assert "but this gate plays flux_pulse_qubit" in by["cz_wrong_qubit"]
    assert "qubit_pairs.q1-2.macros.cz_flat" in rep["verified"]["own_code"]
    assert prev.get("ok"), prev
    assert st["qubit_pairs"]["q1-2"]["macros"]["cz_flat"]["__class__"] == CZ
    html = _plan_rows(tmp_path, out)
    assert "qubit_pairs.q1-2.macros.cz_flat" in html, "the CZ plan does not list the added macro"
    (tmp_path / "cz_plan_official.html").write_text(html, encoding="utf-8")


def test_real_build_lab_two_flux_with_two_snz(tmp_path):
    from tests.test_lab_additions_p2 import _env
    snz = "quam_config.two_flux_gate.SNZTwoFluxPulse"
    two = "quam_config.two_flux_gate.CZGateTwoFlux"

    def adds_for(st):
        return [
            {"kind": "pulse", "target": "qubits.q1.z.operations", "name": "cz2_c", "class": snz,
             "fields": {"amplitude": 0.1, "flat_length": 20}},
            {"kind": "pulse", "target": "qubits.q2.z.operations", "name": "cz2_t", "class": snz,
             "fields": {"amplitude": 0.05, "flat_length": 20}},
            {"kind": "macro", "target": "qubit_pairs.q1-2.macros", "name": "cz_two", "class": two,
             "fields": {"flux_pulse_qubit": "#/qubits/q1/z/operations/cz2_c",
                        "flux_pulse_target": "#/qubits/q2/z/operations/cz2_t"}},
            # the lab gate's own contract: both lines agree on flat_length
            {"kind": "pulse", "target": "qubits.q2.z.operations", "name": "cz2_t_long", "class": snz,
             "fields": {"amplitude": 0.05, "flat_length": 40}},
            {"kind": "macro", "target": "qubit_pairs.q1-2.macros", "name": "cz_two_mismatch", "class": two,
             "fields": {"flux_pulse_qubit": "#/qubits/q1/z/operations/cz2_c",
                        "flux_pulse_target": "#/qubits/q2/z/operations/cz2_t_long"}},
        ]
    out, rep, prev, st = _real(tmp_path, _env("QSM_LAB_ENV_PYTHON"), adds_for,
                               modules=["quam_config.two_flux_gate"])
    added = [e["name"] for e in rep["added"]]
    assert added == ["cz2_c", "cz2_t", "cz_two", "cz2_t_long"], [(e["name"], e["reason"]) for e in rep["refused"]]
    by = {e["name"]: e["reason"] for e in rep["refused"]}
    assert "environment refused it" in by["cz_two_mismatch"] and "own code" in by["cz_two_mismatch"]
    assert "qubit_pairs.q1-2.macros.cz_two" in rep["verified"]["own_code"]
    assert prev.get("ok"), prev
    html = _plan_rows(tmp_path, out)
    assert "qubit_pairs.q1-2.macros.cz_two" in html and "two-flux" in html
    (tmp_path / "cz_plan_lab.html").write_text(html, encoding="utf-8")
    from quam_state_manager.core import cz_gate_plan as czp
    row = next(r for r in czp.operations(st)[0] if r["path"].endswith("macros.cz_two"))
    assert row["kind"] == "two-flux"
    assert {ln["pulse_field"] for ln in row["lines"]} == {"flux_pulse_qubit", "flux_pulse_target"}


# ---------------------------------------------------------------------------
# generate.js wiring (source pins: the renderers themselves run under jsdom above)
# ---------------------------------------------------------------------------

_GEN = (Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "static"
        / "generate.js").read_text(encoding="utf-8")


def test_result_box_follows_the_additions_outcome():
    i = _GEN.index("window.GenAdditions.outcome(res.additions)")
    block = _GEN[i - 400:i + 700]
    assert 'aoc === "mixed"' in block and "gen-build-confirm" in block
    assert 'aoc === "refused"' in block and "gen-build-error" in block


def test_review_verdict_names_the_additions_and_lists_them():
    i = _GEN.index("var addInfo = res.additions")
    block = _GEN[i:i + 900]
    assert '" Additions: "' in block and "h.textContent +=" in block
    assert "window.GenAdditions.renderReview(box, addInfo, state.spec)" in block
