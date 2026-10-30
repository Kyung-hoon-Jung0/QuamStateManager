"""Goldens of the three add paths, recorded before any extraction touches them.

A -- the Generate wizard's add forms: ``spec.additions`` plus the
     ``populate.pairs`` side effects (``add_paths_golden_a.cjs`` under jsdom).
B -- the Pulses page create flow: create (qubit channel, gate slot), copy and
     Gaussian CZ (plain, refused, overwrite): the ordered modifier operations
     each request makes, plus the option list each form offers.
C -- the pair page's Add gate: every gate type's ordered modifier operations
     or refusal, plus the option list the form offers.

Each is recorded on three synthetic chip shapes. Later refactors (shared name
rules, one gate/pulse catalogue, the server-side gate plan) must keep these
byte-identical; an intentional change re-records them with
``SM_UPDATE_ADD_GOLDENS=1`` and says so in its commit.
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from quam_state_manager.core import modifier as _modmod
from quam_state_manager.web.app import create_app

HERE = Path(__file__).parent
GOLDEN = HERE / "golden"
UPDATE = os.environ.get("SM_UPDATE_ADD_GOLDENS") == "1"

sys.path.insert(0, str(HERE))
from cr_fixtures import make_flavor_b  # noqa: E402

_WIRING = {"wiring": {}, "network": {"host": "1.1.1.1", "cluster_name": "C1"}}
_FLUX_LINE = "quam_builder.architecture.superconducting.components.flux_line.FluxLine"


def _flux_chip(*, coupler: bool, moving: str) -> dict:
    qubits = {}
    for q, f in (("q1", 5.0e9), ("q2", 5.2e9)):
        qubits[q] = {"id": q, "f_01": f, "anharmonicity": -2.0e8,
                     "xy": {"operations": {"x180": {"amplitude": 0.3, "length": 40}}},
                     "z": {"__class__": _FLUX_LINE,
                           "operations": {"const": {"amplitude": 0.1, "length": 100}}}}
    pair = {"id": "q1-2", "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
            "moving_qubit": moving,
            "macros": {
                "cz_flattop": {"flux_pulse_qubit": {"amplitude": 0.31, "flat_length": 60},
                               "coupler_flux_pulse": ({"amplitude": 0.12} if coupler else None)},
                "cz_empty": {"flux_pulse_qubit": None, "coupler_flux_pulse": None},
            }}
    if coupler:
        pair["coupler"] = {"id": "c12", "operations": {"const": {"amplitude": 0.2}}}
    return {"qubits": qubits, "qubit_pairs": {"q1-2": pair}, "active_qubit_names": ["q1", "q2"]}


def _shapes() -> dict[str, tuple[dict, dict, str]]:
    cr_state, cr_wiring = make_flavor_b(with_zz=True)
    cr_pair = next(iter(cr_state["qubit_pairs"]))
    return {
        "flux_coupler": (_flux_chip(coupler=True, moving="control"), _WIRING, "q1-2"),
        "flux_nocoupler_target": (_flux_chip(coupler=False, moving="target"), _WIRING, "q1-2"),
        "cr_zz": (cr_state, cr_wiring, cr_pair),
    }


# -- recording the modifier ------------------------------------------------

class _Recorder:
    def __init__(self):
        self.ops: list = []
        self.depth = 0
        self.gids: dict = {}

    def gid(self, g):
        if g is None:
            return None
        return self.gids.setdefault(g, f"g{len(self.gids)}")


def _wrap(rec: _Recorder, name: str, orig):
    def run(self, *args, **kw):
        if rec.depth == 0:
            path = args[0] if args else kw.get("dot_path") or kw.get("old_path")
            if name == "batch_set":
                path, value = None, args[0] if args else kw.get("updates")
            elif name == "rename_subtree":
                value = args[1] if len(args) > 1 else kw.get("new_path")
            elif name == "delete_subtree":
                value = None
            else:
                value = args[1] if len(args) > 1 else kw.get("value", kw.get("new_value"))
            rec.ops.append([name, path, json.loads(json.dumps(value, sort_keys=True, default=str)),
                            rec.gid(kw.get("group_id"))])
        rec.depth += 1
        try:
            return orig(self, *args, **kw)
        finally:
            rec.depth -= 1
    return run


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    for name in ("set_value", "create_subtree", "delete_subtree", "rename_subtree", "batch_set"):
        monkeypatch.setattr(_modmod.Modifier, name, _wrap(rec, name, getattr(_modmod.Modifier, name)))
    return rec


def _client(tmp_path: Path, name: str, state: dict, wiring: dict):
    folder = tmp_path / name
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / (name + "_inst")))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    return c


# -- what a form offers -----------------------------------------------------

_SELECT = re.compile(r"<select\b([^>]*)>(.*?)</select>", re.S)
_OPTION = re.compile(r"<option\b([^>]*)>(.*?)</option>", re.S)
_ISLAND = re.compile(r'<script id="([^"]+)" type="application/json">(.*?)</script>', re.S)
_ATTR = re.compile(r'\b(name|id|value)="([^"]*)"')


def _options(page: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for attrs, body in _SELECT.findall(page):
        a = dict(_ATTR.findall(attrs))
        key = a.get("name") or a.get("id") or "?"
        vals = []
        for oattrs, otext in _OPTION.findall(body):
            oa = dict(_ATTR.findall(oattrs))
            vals.append(oa.get("value", re.sub(r"\s+", " ", _html.unescape(re.sub("<[^>]+>", "", otext))).strip()))
        while key in out:
            key += "+"
        out[key] = vals
    for iid, body in _ISLAND.findall(page):
        try:
            out["json:" + iid] = json.loads(_html.unescape(body))
        except ValueError:
            out["json:" + iid] = body
    return out


def _message(body: bytes) -> str:
    text = re.sub(r"<[^>]+>", " ", body.decode("utf-8", "replace"))
    return re.sub(r"\s+", " ", _html.unescape(text)).strip()[:400]


def _post(c, rec, url, data):
    rec.ops = []
    rec.gids = {}
    r = c.post(url, data=data)
    out = {"url": url, "data": data, "status": r.status_code, "ops": rec.ops}
    if r.status_code != 200:
        out["message"] = _message(r.data)
    return out


# -- B: the Pulses page create flow ----------------------------------------

def _record_b(c, rec, pair: str, shape: str) -> dict:
    forms = {kind: _options(c.get("/pulse/new" + ("?kind=" + kind if kind else "")).data.decode())
             for kind in ("", "copy", "gaussian_cz")}
    q = "q1" if shape != "cr_zz" else "q0"
    steps = [
        _post(c, rec, "/api/pulse/create", {
            "target_kind": "qubit", "qubit": q, "channel": "xy", "op_name": "gauss_probe",
            "pulse_type": "GaussianPulse", "length": "40", "amplitude": "0.1", "sigma": "8.0"}),
        _post(c, rec, "/api/pulse/create", {
            "target_kind": "qubit", "qubit": q, "channel": "z", "op_name": "sq_flux",
            "pulse_type": "SquarePulse", "length": "60", "amplitude": "0.05"}),
        _post(c, rec, "/api/pulse/create", {
            "target_kind": "qubit", "qubit": q, "channel": "xy", "op_name": "1bad",
            "pulse_type": "SquarePulse", "length": "60", "amplitude": "0.05"}),
        _post(c, rec, "/api/pulse/create", {
            "target_kind": "pair", "pair": pair, "gate": "cz_empty", "slot": "flux_pulse_qubit",
            "pulse_type": "SquarePulse", "length": "48", "amplitude": "0.2"}),
        _post(c, rec, "/api/pulse/create", {
            "target_kind": "pair", "pair": pair, "gate": "cz_empty", "slot": "coupler_flux_pulse",
            "pulse_type": "SquarePulse", "length": "48", "amplitude": "0.1"}),
        _post(c, rec, "/api/pulse/copy", {
            "path": f"qubits.{q}.xy.operations.gauss_probe", "target_kind": "qubit",
            "qubit": "q2", "channel": "xy", "op_name": "gauss_copy"}),
        _post(c, rec, "/api/pulse/copy", {
            "path": f"qubits.{q}.xy.operations.gauss_probe", "target_kind": "qubit",
            "qubit": q, "channel": "xy", "op_name": "gauss_probe"}),
        _post(c, rec, "/api/pulse/gaussian-cz", {"pair_id": pair}),
        _post(c, rec, "/api/pulse/gaussian-cz", {"pair_id": pair}),
        _post(c, rec, "/api/pulse/gaussian-cz", {"pair_id": pair, "overwrite": "1",
                                                 "padding_length": "16"}),
    ]
    return {"forms": forms, "steps": steps}


# -- C: the pair page's Add gate --------------------------------------------

def _record_c(c, rec, pair: str) -> dict:
    from quam_state_manager.web import routes
    form = _options(c.get(f"/pair/{pair}/gate/new").data.decode())
    steps = []
    types = {**routes._GATE_TYPES, **routes._ENV_GATE_TYPES}
    for gt in sorted(types):
        data = {"gate_name": "g_" + gt, "gate_type": gt}
        for fname, _label, default, _kind in types[gt]["fields"]:
            data[fname] = str(default)
        steps.append(_post(c, rec, f"/pair/{pair}/gate", data))
    steps.append(_post(c, rec, f"/pair/{pair}/gate", {"gate_name": "1bad", "gate_type": "cr_gate"}))
    steps.append(_post(c, rec, f"/pair/{pair}/gate", {"gate_name": "g_x", "gate_type": "nope"}))
    steps.append(_post(c, rec, f"/pair/{pair}/gate", {"gate_name": "g_cr_gate", "gate_type": "cr_gate"}))
    steps.append(_post(c, rec, f"/pair/{pair}/gate", {"gate_name": "g_typo", "gate_type": "cr_gate",
                                                      "qc_correction_phase": "abc"}))
    return {"form": form, "steps": steps}


def _compare(name: str, got) -> None:
    path = GOLDEN / name
    text = json.dumps(got, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if UPDATE or not path.exists():
        path.write_text(text, encoding="utf-8")
        if not UPDATE:
            pytest.fail(f"{name} did not exist; recorded it -- re-run")
        return
    want = path.read_text(encoding="utf-8")
    assert text == want, f"{name} changed; diff it against the recorded golden"


@pytest.mark.parametrize("shape", list(_shapes()))
def test_golden_b_pulses_create_copy_gaussian(tmp_path, recorder, shape):
    state, wiring, pair = _shapes()[shape]
    c = _client(tmp_path, shape, state, wiring)
    _compare(f"add_paths_b_{shape}.json", _record_b(c, recorder, pair, shape))


@pytest.mark.parametrize("shape", list(_shapes()))
def test_golden_c_pair_add_gate(tmp_path, recorder, shape):
    state, wiring, pair = _shapes()[shape]
    c = _client(tmp_path, shape, state, wiring)
    _compare(f"add_paths_c_{shape}.json", _record_c(c, recorder, pair))


def test_the_recorder_sees_every_write(tmp_path, recorder):
    """The goldens are only as good as the recorder: a create that writes must
    show up as at least one operation, a refusal as none."""
    state, wiring, pair = _shapes()["flux_coupler"]
    c = _client(tmp_path, "rec", state, wiring)
    ok = _post(c, recorder, "/api/pulse/gaussian-cz", {"pair_id": pair})
    assert ok["status"] == 200 and len(ok["ops"]) >= 4 and len({o[3] for o in ok["ops"]}) == 1
    bad = _post(c, recorder, "/api/pulse/gaussian-cz", {"pair_id": pair})
    assert bad["status"] == 409 and bad["ops"] == []


def test_golden_a_wizard_additions():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    p = subprocess.run(["node", str(HERE / "add_paths_golden_a.cjs")],
                       capture_output=True, text=True, encoding="utf-8")
    if p.returncode == 2 and "jsdom not installed" in p.stderr:
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, p.stderr
    got = json.loads(p.stdout)
    assert set(got) == {"chain3_coupler", "pair2_plain", "lettered_coupler"}
    _compare("add_paths_a.json", got)
