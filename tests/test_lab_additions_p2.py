"""Lab additions, phase 2: pulse additions in the Generate Config wizard.

Covers the spec slot (validate_spec), the SM-side JSON pass, the per-addition
refusal (degrade-only), the env-verify orchestration, the build route + spec
sidecar, the wizard section under jsdom, and real builds in the official env
and in a lab env (both skipped when absent).
"""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import additions, config_generator, regen_spec

QB = "quam_builder.architecture.superconducting.components.pulses."
DRAG = QB + "DragCosinePulse"
FLAT = "quam.components.pulses.FlatTopGaussianPulse"


def _f(raw, has_default=False, kind="plain", default_repr=None, optional=False):
    return {"raw": raw, "has_default": has_default, "kind": kind,
            "default_repr": default_repr, "optional": optional}


MAN = {"versions": {"quam_builder": "0.6.0"}, "classes": {
    DRAG: {"role": "pulse", "origin": "official", "abstract": False, "fields": {
        "length": _f("<class 'int'>"), "amplitude": _f("<class 'float'>"),
        "anharmonicity": _f("<class 'float'>"),
        "detuning": _f("<class 'float'>", True, default_repr="0.0")}},
    FLAT: {"role": "pulse", "origin": "official", "abstract": False, "fields": {
        "length": _f("int"), "amplitude": _f("float"), "flat_length": _f("int")}},
    "quam.components.pulses.BaseReadoutPulse": {"role": "pulse", "abstract": True, "fields": {}},
    "lab.gates.LabGate": {"role": "macro_pair", "origin": "lab", "fields": {}},
    "lab.flux.LabPulse": {"role": "pulse", "origin": "lab", "abstract": False, "fields": {
        "amplitude": _f("float"), "flat_length": _f("int"),
        "unipolar": _f("bool", True, default_repr="False")}},
}}


def _spec(adds=None):
    s = {"qubits": ["q1", "q2"], "qubit_pairs": [["q1", "q2"]],
         "lines": [{"element": "q1", "line": "drive"}, {"element": "q1", "line": "flux"},
                   {"element": "q2", "line": "drive"}, {"element": "q1-q2", "line": "coupler"}]}
    if adds is not None:
        s["additions"] = adds
    return s


def _state():
    return {"__class__": "x.Root", "qubits": {
        "q1": {"anharmonicity": -2e8, "xy": {"operations": {"x180": {"length": 40}}},
               "z": {"operations": {}}},
        "q2": {"anharmonicity": -2e8, "xy": {"operations": {}}}},
        "qubit_pairs": {"q1-2": {"coupler": {"operations": {}}}}}


def _drag(name="x180_w", target="qubits.q1.xy.operations", **over):
    f = {"length": 48, "amplitude": 0.1, "anharmonicity": "#/qubits/q1/anharmonicity"}
    f.update(over)
    return {"kind": "pulse", "target": target, "name": name, "class": DRAG, "fields": f}


# ---------------------------------------------------------------------------
# validate_spec: shape only
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("adds, needle", [
    ("x", "must be a list"),
    ([1], "must be an object"),
    ([{"kind": "gate", "target": "qubits.q1.xy.operations", "name": "a", "class": "a.B"}], "unknown kind"),
    ([{"kind": "pulse", "target": "qubits.q1.xy", "name": "a", "class": "a.B"}], "operations dict"),
    ([{"kind": "pulse", "target": "qubits.q1.xy.operations", "name": "a.b", "class": "a.B"}], "name must"),
    ([{"kind": "pulse", "target": "qubits.q1.xy.operations", "name": "a", "class": "B"}], "dotted"),
    ([{"kind": "pulse", "target": "qubits.q1.xy.operations", "name": "a", "class": "a.B", "fields": 3}], "fields must"),
    ([_drag(), _drag()], "added twice"),
])
def test_structural_errors(adds, needle):
    errs = additions.structural_errors({"additions": adds})
    assert any(needle in e for e in errs), errs


def test_validate_spec_carries_the_additions_errors():
    errs = config_generator.validate_spec({"additions": "x"})
    assert "additions: must be a list" in errs


def test_no_additions_no_errors():
    assert additions.structural_errors({}) == []
    assert additions.structural_errors({"additions": [_drag()]}) == []


# ---------------------------------------------------------------------------
# per-addition refusals
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("add, needle", [
    (_drag(target="qubits.q9.xy.operations"), "qubit q9 is not in this chip"),
    (_drag(target="qubits.q2.z.operations"), "has no flux line"),
    (_drag(target="qubit_pairs.q2-3.coupler.operations"), "pair q2-3 is not in this chip"),
    (dict(_drag(), **{"class": "quam.components.pulses.NoSuch"}), "not in this environment"),
    (dict(_drag(), **{"class": "lab.gates.LabGate"}), "not a pulse"),
    (dict(_drag(), **{"class": "quam.components.pulses.BaseReadoutPulse"}), "abstract"),
    (_drag(sigma=3), "sigma is not a field"),
    ({"kind": "pulse", "target": "qubits.q1.xy.operations", "name": "p", "class": DRAG,
      "fields": {"length": 48}}, "required fields missing: amplitude, anharmonicity"),
    (_drag(length=48.5), "does not fit its type int"),
    (_drag(length=True), "does not fit its type int"),
    (_drag(amplitude="big"), "does not fit its type float"),
    (_drag(length=None), "cannot be empty"),
])
def test_one_bad_addition_is_refused_alone(add, needle):
    st = _state()
    rep = additions.apply_additions(st, _spec([_drag("good"), add]), MAN)
    assert [e["name"] for e in rep["added"]] == ["good"]
    assert len(rep["refused"]) == 1 and needle in rep["refused"][0]["reason"], rep
    assert "good" in st["qubits"]["q1"]["xy"]["operations"]


def test_a_pointer_literal_is_accepted_for_any_kind():
    rep = additions.apply_additions(_state(), _spec([_drag(length="#./amplitude")]), MAN)
    assert rep["added"] and not rep["refused"]


def test_name_taken_and_missing_target():
    rep = additions.apply_additions(_state(), _spec([
        _drag(name="x180"),
        _drag(name="c", target="qubit_pairs.q1-2.cross.operations")]), MAN)
    reasons = [e["reason"] for e in rep["refused"]]
    assert "already has an operation named x180" in reasons[0]
    assert "does not exist in the built chip" in reasons[1]


def test_dangling_pointer_and_pointer_to_another_addition():
    adds = [_drag(name="a"), _drag(name="b", anharmonicity="#../a/anharmonicity"),
            _drag(name="c", anharmonicity="#/qubits/q9/anharmonicity"),
            _drag(name="d", anharmonicity="#../c/anharmonicity")]
    st = _state()
    rep = additions.apply_additions(st, _spec(adds), MAN)
    assert [e["name"] for e in rep["added"]] == ["a", "b"]
    by = {e["name"]: e["reason"] for e in rep["refused"]}
    assert "points at nothing" in by["c"]
    assert "an addition that was refused" in by["d"]   # stranded by c's refusal (fixpoint)
    assert "c" not in st["qubits"]["q1"]["xy"]["operations"]


def test_no_manifest_is_not_a_blocker_but_is_said():
    rep = additions.apply_additions(_state(), _spec([_drag(sigma=3)]), None)
    assert rep["added"] and "not available" in rep["notes"][0]


def test_body_is_class_plus_fields_only():
    st = _state()
    additions.apply_additions(st, _spec([_drag()]), MAN)
    assert st["qubits"]["q1"]["xy"]["operations"]["x180_w"] == {
        "__class__": DRAG, "length": 48, "amplitude": 0.1,
        "anharmonicity": "#/qubits/q1/anharmonicity"}


def test_lab_origin_and_schema_hash():
    assert additions.origin_of("lab.flux.LabPulse", MAN) == "lab"
    assert additions.origin_of("other_lab.P", None) == "lab"
    assert additions.origin_of(FLAT, None) == "official"
    h = additions.schema_hash(MAN, DRAG)
    m2 = copy.deepcopy(MAN)
    m2["classes"][DRAG]["fields"]["sigma"] = _f("float")
    assert h and h != additions.schema_hash(m2, DRAG)
    st = additions.stamp(_spec([_drag()]), MAN, "py")
    assert st[0]["schema_hash"] == h and st[0]["env"]["versions"]["quam_builder"] == "0.6.0"


# ---------------------------------------------------------------------------
# apply_and_verify orchestration (fake env)
# ---------------------------------------------------------------------------

def _built(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    (d / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (d / "wiring.json").write_text("{}", encoding="utf-8")
    return d


def _fake_verify(bad: set, calls: list):
    def verify(python, folders, timeout=600):
        calls.append(sorted(folders))
        out = {}
        for k, item in folders.items():
            st = json.loads(Path(item["folder"], "state.json").read_text(encoding="utf-8"))
            names = set(st["qubits"]["q1"]["xy"]["operations"]) | set(st["qubits"]["q1"]["z"]["operations"])
            hit = names & bad
            out[k] = {"ok": not hit, "error": f"generate_config: ValueError: {sorted(hit)}" if hit else None}
        return {"ok": True, "folders": out}
    return verify


def test_verify_all_ok_writes_once(tmp_path):
    d = _built(tmp_path)
    calls = []
    rep = additions.apply_and_verify("py", d, _spec([_drag("a"), _drag("b")]), MAN,
                                     verify=_fake_verify(set(), calls))
    assert [e["name"] for e in rep["added"]] == ["a", "b"] and calls == [["all"]]
    ops = json.loads((d / "state.json").read_text(encoding="utf-8"))["qubits"]["q1"]["xy"]["operations"]
    assert {"a", "b"} <= set(ops)


def test_verify_env_refusal_removes_that_addition_alone(tmp_path):
    d = _built(tmp_path)
    calls = []
    rep = additions.apply_and_verify("py", d, _spec([_drag("a"), _drag("b")]), MAN,
                                     verify=_fake_verify({"b"}, calls))
    assert [e["name"] for e in rep["added"]] == ["a"]
    assert rep["refused"][0]["name"] == "b" and "environment refused it" in rep["refused"][0]["reason"]
    assert calls == [["all"], ["0", "1"], ["final"]]
    ops = json.loads((d / "state.json").read_text(encoding="utf-8"))["qubits"]["q1"]["xy"]["operations"]
    assert "a" in ops and "b" not in ops


def test_a_check_that_cannot_run_keeps_nothing(tmp_path):
    d = _built(tmp_path)
    before = (d / "state.json").read_text(encoding="utf-8")
    rep = additions.apply_and_verify("py", d, _spec([_drag("a")]), MAN,
                                     verify=lambda *a, **k: {"ok": False, "error": "boom"})
    assert not rep["added"] and "not verified: boom" in rep["refused"][0]["reason"]
    assert (d / "state.json").read_text(encoding="utf-8") == before


def test_no_env_keeps_nothing(tmp_path):
    d = _built(tmp_path)
    rep = additions.apply_and_verify(None, d, _spec([_drag("a")]), MAN)
    assert not rep["added"] and "no environment" in rep["refused"][0]["reason"]


def test_lab_class_gets_its_own_code_checked(tmp_path):
    d = _built(tmp_path)
    seen = {}

    def verify(python, folders, timeout=600):
        seen.update(folders)
        return {"ok": True, "folders": {k: {"ok": True} for k in folders}}
    lab = {"kind": "pulse", "target": "qubits.q1.z.operations", "name": "lp",
           "class": "lab.flux.LabPulse", "fields": {"amplitude": 0.1, "flat_length": 20}}
    rep = additions.apply_and_verify("py", d, _spec([_drag("a"), lab]), MAN, verify=verify)
    assert seen["all"]["own_code"] == ["qubits.q1.z.operations.lp"]
    assert rep["verified"]["own_code"] == ["qubits.q1.z.operations.lp"]


# ---------------------------------------------------------------------------
# the build route + sidecar
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    from quam_state_manager.web.app import create_app
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    return app.test_client()


def test_build_route_applies_reports_and_keeps_the_sidecar(client, tmp_path, monkeypatch):
    from quam_state_manager.core import role_manifest
    out = tmp_path / "chip"

    def fake_run(python, mode, spec, out_dir, timeout=600):
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
        (Path(out_dir) / "wiring.json").write_text("{}", encoding="utf-8")
        return {"ok": True, "status": "ok", "result": {"qubits": ["q1", "q2"], "qubit_pairs": []}}
    monkeypatch.setattr(config_generator, "get_selected_env", lambda _p: "py")
    monkeypatch.setattr(config_generator, "validate_spec", lambda s: additions.structural_errors(s))
    monkeypatch.setattr(config_generator, "probe_capabilities", lambda *a, **k: {})
    monkeypatch.setattr(config_generator, "run_generator", fake_run)
    monkeypatch.setattr(config_generator, "annotate_unplayable", lambda *a, **k: None)
    monkeypatch.setattr(role_manifest, "probe_roles", lambda *a, **k: {"ok": True, "manifest": MAN})
    monkeypatch.setattr(config_generator, "run_additions_verify",
                        lambda py, folders, timeout=600: {"ok": True, "folders": {
                            k: {"ok": True} for k in folders}})
    spec = _spec([_drag("a"), _drag("b", length=1.5)])
    r = client.post("/generate/build", json={"spec": spec, "output_path": str(out)})
    body = r.get_json()
    assert body["ok"], body
    rep = body["additions"]
    assert [e["name"] for e in rep["added"]] == ["a"]
    assert "does not fit" in rep["refused"][0]["reason"]
    side = json.loads((out / ".regen" / "generate_spec.json").read_text(encoding="utf-8"))
    sa = side["spec"]["additions"] if "spec" in side else side["additions"]
    assert sa[0]["schema_hash"] == additions.schema_hash(MAN, DRAG)
    assert sa[0]["env"]["python"] == "py"
    st, wr = json.loads((out / "state.json").read_text()), {}
    assert regen_spec.load_spec_sidecar(out, st, json.loads((out / "wiring.json").read_text())) is not None


def test_roles_route_lists_pulse_classes(client, monkeypatch):
    from quam_state_manager.core import role_manifest
    monkeypatch.setattr(config_generator, "get_selected_env", lambda _p: "py")
    monkeypatch.setattr(role_manifest, "roles_for_env", lambda *a, **k: MAN)
    body = client.get("/generate/roles").get_json()
    assert body["ok"] and DRAG in body["pulses"] and "lab.flux.LabPulse" in body["pulses"]
    assert "quam.components.pulses.BaseReadoutPulse" not in body["pulses"]   # abstract
    monkeypatch.setattr(role_manifest, "roles_for_env", lambda *a, **k: None)
    body = client.get("/generate/roles").get_json()
    assert not body["ok"] and body["cold"]


# ---------------------------------------------------------------------------
# the wizard section (jsdom)
# ---------------------------------------------------------------------------

def test_wizard_selfcheck():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    p = subprocess.run(["node", str(Path(__file__).with_name("gen_additions_selfcheck.cjs"))],
                       capture_output=True, text=True, encoding="utf-8")
    if p.returncode == 2 and "jsdom not installed" in p.stderr:
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, p.stdout + p.stderr
    assert "0 failed" in p.stdout


# ---------------------------------------------------------------------------
# real builds
# ---------------------------------------------------------------------------

def _env(var, default_name=None):
    """An interpreter from env var *var*, else a conda env named *default_name*."""
    import os
    p = os.environ.get(var)
    if not p and default_name:
        for env in config_generator.discover_envs():
            if Path(env["python"]).parent.name == default_name:
                p = env["python"]
                break
    return p if p and Path(p).is_file() else None


def _real_spec():
    from tests.test_script_emitter_live import _spec as base
    return base()


def _real(tmp_path, py, adds, modules=()):
    from quam_state_manager.core import role_manifest
    if not py:
        pytest.skip("environment absent")
    out = tmp_path / "chip"
    spec = _real_spec()
    o = config_generator.run_generator(py, "build", spec, out, timeout=600)
    assert o.get("ok"), o
    spec["additions"] = adds
    classes = sorted({a["class"] for a in adds})
    man = role_manifest.probe_roles(py, None, state_classes=classes, modules=list(modules))["manifest"]
    rep = additions.apply_and_verify(py, out, spec, man)
    # an independent fresh process: the previewer's own generate_config()
    prev = config_generator.run_config_preview(py, out)
    return rep, prev, json.loads((out / "state.json").read_text(encoding="utf-8"))


def test_real_build_official_env(tmp_path):
    adds = [
        {"kind": "pulse", "target": "qubits.q1.xy.operations", "name": "x180_wide", "class": DRAG,
         "fields": {"length": 64, "axis_angle": 0.0, "amplitude": 0.1, "alpha": 0.0,
                    "anharmonicity": "#/qubits/q1/anharmonicity"}},
        {"kind": "pulse", "target": "qubits.q1.z.operations", "name": "flat_cz", "class": FLAT,
         "fields": {"length": 40, "amplitude": 0.05, "flat_length": 24}},
        # faults: missing required field, dangling pointer, unknown class, env refusal
        {"kind": "pulse", "target": "qubits.q2.xy.operations", "name": "f_missing", "class": DRAG,
         "fields": {"length": 64}},
        {"kind": "pulse", "target": "qubits.q2.xy.operations", "name": "f_ptr", "class": DRAG,
         "fields": {"length": 64, "axis_angle": 0.0, "amplitude": 0.1, "alpha": 0.0,
                    "anharmonicity": "#/qubits/q9/anharmonicity"}},
        {"kind": "pulse", "target": "qubits.q2.xy.operations", "name": "f_cls",
         "class": "quam.components.pulses.NoSuchPulse", "fields": {"length": 16}},
        {"kind": "pulse", "target": "qubits.q2.z.operations", "name": "f_env", "class": FLAT,
         "fields": {"length": 20, "amplitude": 0.05, "flat_length": 40}},
    ]
    rep, prev, st = _real(tmp_path, _env("QSM_OFFICIAL_PYTHON", "qsm_official"), adds)
    assert [e["name"] for e in rep["added"]] == ["x180_wide", "flat_cz"], rep
    by = {e["name"]: e["reason"] for e in rep["refused"]}
    assert "required fields missing" in by["f_missing"]
    assert "points at nothing" in by["f_ptr"]
    assert "not in this environment" in by["f_cls"]
    assert "environment refused it" in by["f_env"]
    assert prev.get("ok"), prev
    assert "x180_wide" in st["qubits"]["q1"]["xy"]["operations"]
    assert "flat_cz" in st["qubits"]["q1"]["z"]["operations"]
    assert "f_env" not in st["qubits"]["q2"]["z"]["operations"]
    cfg = json.dumps((prev.get("result") or {}).get("config") or {})
    assert "x180_wide" in cfg and "flat_cz" in cfg


def test_real_build_lab_env_snz(tmp_path):
    snz = "quam_config.two_flux_gate.SNZTwoFluxPulse"
    adds = [
        {"kind": "pulse", "target": "qubits.q1.z.operations", "name": "snz_cz", "class": snz,
         "fields": {"amplitude": 0.1, "flat_length": 20}},
        {"kind": "pulse", "target": "qubits.q2.z.operations", "name": "snz_odd", "class": snz,
         "fields": {"amplitude": 0.1, "flat_length": 21}},
    ]
    # a lab env whose quam_config carries the two-flux pulse classes; named by
    # QSM_LAB_ENV_PYTHON (no default: lab env names stay out of the code)
    rep, prev, st = _real(tmp_path, _env("QSM_LAB_ENV_PYTHON"), adds,
                          modules=["quam_config.two_flux_gate"])
    assert [e["name"] for e in rep["added"]] == ["snz_cz"], rep
    assert rep["verified"]["own_code"] == ["qubits.q1.z.operations.snz_cz"]
    assert "must be even" in rep["refused"][0]["reason"]
    assert prev.get("ok"), prev
    assert st["qubits"]["q1"]["z"]["operations"]["snz_cz"]["__class__"] == snz
