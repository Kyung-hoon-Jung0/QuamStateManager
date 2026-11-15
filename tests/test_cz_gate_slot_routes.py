"""The New CZ gate routes for a slot gate class SM made.

A fake runner stands in for the lab's code (the class save worker and the
gate check); the drafts, the descriptor, the spectator plan, the token and
the write are the real ones. One test runs the real lab worker and check
when SM_LAB_ENV_PYTHON names a lab environment."""
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

from quam_state_manager.core import config_generator
from quam_state_manager.core import cz_gate_build as cgb
from quam_state_manager.core import cz_gate_create
from quam_state_manager.core import pulse_class_drafts as D
from quam_state_manager.core import pulse_class_watch as W
from quam_state_manager.core import quam_config_save as Q
from quam_state_manager.core import slot_gate_plan as sgp
from quam_state_manager.core import slot_gate_template as sgt
from quam_state_manager.web import cz_gate_routes as czr
from tests.test_cz_gate_build import _chip
from tests.test_cz_gate_routes import CHECK, CREATE, PLAN, SQUARE, App, _body, _fake

SLOT_ROUTE = "/api/pulse/cz-gate/slot-class"
DIALOG = "/pulse/cz-gate/dialog?pair=q1-2"
NAME = "SlotGate"
QCLASS = f"quam_config.{NAME}.{NAME}"
UNIT = sgt.SPECTATOR_UNIT_OP
LAB_PY = Path(os.environ.get("SM_LAB_ENV_PYTHON") or "__no_lab_env__")


def _state(n=5, units=None):
    st = _chip(n)
    for q, amp in ({"q3": 0.25} if units is None else units).items():
        st["qubits"][q]["z"]["operations"][UNIT] = {"id": UNIT, "amplitude": amp, "length": 16,
                                                    "__class__": SQUARE}
    return st


class Env:
    def __init__(self, tmp, monkeypatch, state=None, real_lab=False):
        folder = Path(tmp) / "source" / "quam_config"
        folder.mkdir(parents=True)
        (folder / "__init__.py").write_text("", encoding="utf-8")
        monkeypatch.setattr(config_generator, "known_interpreter", lambda *a, **k: True)
        monkeypatch.setattr(config_generator, "get_lab_code_dir", lambda *a: str(folder.parent))
        if not real_lab:
            def runner(python, req, **kwargs):
                if req["action"] == "discover":
                    return {"ok": True, "folder": str(folder.resolve()), "site_packages": []}
                if req["action"] == "load":
                    return {"ok": True, "chip_loaded": True}
                data = base64.b64decode(req["source"])
                return {"ok": True, "sha256": D.sha256_bytes(data), "chip_loaded": True,
                        "proportional": None}
            monkeypatch.setattr(Q, "_run", runner)
            self.fake = _fake()
            monkeypatch.setattr(czr, "run_check", self.fake)
            monkeypatch.setattr(czr, "_env_python", lambda: "C:/fake/python.exe")
        W._WATCHES.clear()
        self.tmp = Path(tmp)
        self.folder = folder.resolve()
        self.a = App(tmp, state or _state())
        self.c = self.a.c

    def make(self, name=NAME, slots=tuple(sgt.SLOTS), spectators=True, code=200):
        r = self.c.post(SLOT_ROUTE, json={"class_name": name, "slots": list(slots),
                                          "spectators": spectators})
        assert r.status_code == code, r.get_json()
        return r.get_json()

    def save(self, name=NAME, python="py"):
        first = self.c.post("/pulse-class/save-config", json={"cls": name, "python": python}).get_json()
        assert first.get("confirm"), first
        r = self.c.post("/pulse-class/save-config", json={
            "cls": name, "python": python, "confirmed_folder": first["confirm"]["folder"]})
        assert r.status_code == 200, r.get_json()
        return r.get_json()

    @property
    def scope(self):
        from quam_state_manager.web import routes as R
        return R._ctx_chip_key(self.a.ctx)

    def dialog(self):
        html = self.c.get(DIALOG).get_data(as_text=True)
        return json.loads(html.split('id="czg-data">', 1)[1].split("</script>", 1)[0])

    def files(self):
        """Every file under the temp root (chips, instance, lab folder), by bytes."""
        return {str(p.relative_to(self.tmp)): p.read_bytes()
                for p in sorted(self.tmp.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}

    def stored(self):
        return self.files(), self.a.snap()


@pytest.fixture
def env(monkeypatch):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        yield Env(tmp, monkeypatch)
        from quam_state_manager.core.hub_index import close_readers
        close_readers()


def _pulses(spec_slots=sgt.SLOTS, amp=0.1):
    sq = {"class": SQUARE, "fields": {"length": 48, "amplitude": amp}}
    return {sgp.slot_field(s): dict(sq) for s in sgt.SLOTS if s in spec_slots}


def _slot_body(pairs=("q1-2",), rows=None, name="cz_s", slots=sgt.SLOTS):
    b = {"pairs": list(pairs), "gate_name": name, "gate_class": QCLASS, "pulses": _pulses(slots)}
    if rows is not None:
        b["spectators"] = rows
    return b


def _ready(env, **kw):
    env.make(**kw)
    env.save()


def _pair(j, pid="q1-2"):
    return next(p for p in j["pairs"] if p["pair"] == pid)


# ---------------------------------------------------------------- the class list

def test_only_readable_slot_drafts_are_listed_with_presets_and_names(env):
    env.make()
    D.save_draft(env.a.app.instance_path, env.scope, "Broken", sgt.render(
        sgt.SlotSpec("Broken", frozenset({"coupler"}))) + "# edited\n")
    assert env.c.post("/pulse-class/new", json={"cls": "FlatPulse"}).status_code == 200
    data = env.dialog()
    slot = [c for c in data["classes"] if c["layout"] == "slot"]
    assert [c["class"] for c in slot] == [QCLASS]
    c = slot[0]
    assert set(c) == {"class", "label", "layout", "available", "why", "slots", "models",
                      "slot_spec", "spectators"}
    assert c["label"] == f"{NAME} (slot class SM made)" and c["available"] and c["why"] == ""
    assert c["slot_spec"] == {"slots": list(sgt.SLOTS), "spectators": True}
    assert [s["field"] for s in c["slots"]] == [sgp.slot_field(s) for s in sgt.SLOTS]
    assert all(s["required"] and s["pulse_classes"] for s in c["slots"])
    assert c["spectators"] == {"offered": True, "method": "unit", "why": ""}
    assert data["slot_names"] == list(sgt.SLOTS)
    assert [(p["key"], p["slots"]) for p in data["slot_presets"]] == [
        (k, [s for s in sgt.SLOTS if s in v]) for k, v in sgt.PRESETS.items()]
    assert not any("FlatPulse" in str(x["class"]) or "Broken" in str(x["class"])
                   for x in data["classes"])
    table = {x["class"]: x for x in data["classes"] if x["layout"] == "known"}
    assert set(table[cgb.CZ_GATE]) == {"class", "label", "layout", "available", "why", "slots",
                                       "models", "spectators"}
    # fit covers every pair and every class, the slot class included
    assert set(data["fit"]) == {p["id"] for p in data["pairs"]}
    assert all(QCLASS in v for v in data["fit"].values())


def test_candidates_carry_the_unit_and_the_template_range(env, monkeypatch):
    env.make()
    monkeypatch.setattr(sgt, "reachable_level", lambda u: 3.0 * abs(u))   # the template's, at call time
    rows = {r["qubit"]: r for r in next(p for p in env.dialog()["pairs"]
                                        if p["id"] == "q1-2")["spectator_candidates"]}
    assert rows["q3"]["unit"] == 0.25 and rows["q3"]["reachable"] == sgt.reachable_level(0.25) == 0.75
    assert rows["q4"]["unit"] is None and rows["q4"]["reachable"] is None


def test_a_class_without_spectators_is_offered_none_with_the_descriptor_sentence(env):
    env.make(spectators=False)
    c = next(c for c in env.dialog()["classes"] if c["layout"] == "slot")
    desc = sgp.slot_desc(sgt.SlotSpec(NAME, frozenset(sgt.SLOTS), False))
    assert c["spectators"] == {"offered": False, "method": "unit",
                               "why": cgb.spectator_refusal(QCLASS, gate_desc=desc)}
    assert c["spectators"]["why"] == cgb.NO_SPECTATORS_DESCRIBED


def test_the_fit_of_a_slot_class_uses_its_descriptor(monkeypatch):
    st = _state()
    del st["qubit_pairs"]["q2-3"]["coupler"]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        e = Env(tmp, monkeypatch, st)
        e.make()
        fit = e.dialog()["fit"]
        desc = sgp.slot_desc(sgt.SlotSpec(NAME, frozenset(sgt.SLOTS), True))
        why = cgb.class_problem(e.a.store.merged, "q2-3", QCLASS, gate_desc=desc)
        assert why and fit["q2-3"][QCLASS] == why and fit["q1-2"][QCLASS] == ""
        # the plan's class guard names the same reason for its own field
        e.save()
        j = e.c.post(PLAN, json=_slot_body(pairs=("q2-3",))).get_json()
        assert why in _pair(j, "q2-3")["class_refusals"]
        from quam_state_manager.core.hub_index import close_readers
        close_readers()


def test_the_dialog_read_has_no_side_effects(env):
    env.make()
    env.c.get(DIALOG)
    before = env.files()
    one = env.c.get(DIALOG).get_data()
    two = env.c.get(DIALOG).get_data()
    assert one == two and env.files() == before


# ---------------------------------------------------------------- the slot route

@pytest.mark.parametrize("preset", sorted(sgt.PRESETS))
def test_each_preset_makes_a_class_that_the_save_door_saves(env, preset):
    slots = [s for s in sgt.SLOTS if s in sgt.PRESETS[preset]]
    j = env.make(slots=slots)
    assert j == {"ok": True, "cls": NAME, "qclass": QCLASS}
    text = D.load_draft(env.a.app.instance_path, env.scope, NAME).decode("utf-8")
    assert text == sgt.render(sgt.SlotSpec(NAME, frozenset(slots), True))
    assert W.watch_for(env.a.app.instance_path, env.scope, NAME).sha == D.sha256_bytes(text.encode())
    out = env.save()
    assert out["ok"] and (env.folder / f"{NAME}.py").read_text(encoding="utf-8") == text
    assert "chip" not in text.split(sgt.REGION_BEGIN, 1)[1].split("def ", 1)[0]
    j = env.c.post(PLAN, json=_slot_body(slots=slots)).get_json()
    assert _pair(j)["ok"], _pair(j)


def test_validate_sentences_come_back_as_problems(env):
    before = env.stored()
    r = env.c.post(SLOT_ROUTE, json={"class_name": "lower", "slots": [], "spectators": "yes"})
    j = r.get_json()
    want = sgt.validate(sgt.SlotSpec("lower", [], "yes"))
    assert r.status_code == 400 and j["ok"] is False and j["problems"] == want and j["error"] == want[0]
    r = env.c.post(SLOT_ROUTE, json={"class_name": "Good", "slots": [{"x": 1}]})
    assert r.status_code == 400 and r.get_json()["problems"]
    assert env.c.post(SLOT_ROUTE, json={"class_name": 3, "slots": ["coupler"]}).status_code == 400
    assert env.stored() == before


def test_a_taken_name_is_refused_in_one_sentence(env):
    env.c.post("/pulse-class/new", json={"cls": "FlatPulse"})
    for name in ("CZGate", "CouplerCZGate", "SquarePulse", "FlatPulse"):
        j = env.make(name=name, code=409)
        assert j["ok"] is False and j["problems"] == [j["error"]] and name in j["error"]
    (env.folder / "LabGate.py").write_text("class LabGate: pass\n", encoding="utf-8")
    assert "quam_config folder" in env.make(name="LabGate", code=409)["error"]


def test_a_slot_draft_is_replaced_only_while_no_gate_uses_it(env):
    _ready(env)
    env.make(slots=["coupler"])               # unused: replaced
    assert sgt.parse(D.load_draft(env.a.app.instance_path, env.scope, NAME).decode()).spec.slots == {"coupler"}
    env.save()
    j = _check(env, _slot_body(slots=["coupler"]))
    assert env.c.post(CREATE, json={"check_token": j["check_token"],
                                    "plan_sig": j["plan_sig"]}).status_code == 200
    before = env.stored()
    j = env.make(code=409)
    assert j["error"] == czr.NAME_IN_USE.format(name=NAME, count=1)
    assert env.stored() == before


# ---------------------------------------------------------------- planning

def _check(env, body):
    r = env.c.post(CHECK, json=body)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def test_an_unsaved_slot_class_waits_for_its_file(env):
    env.make()
    j = env.c.post(PLAN, json=_slot_body()).get_json()
    assert _pair(j)["refusals"][0].startswith(f"Save {NAME} to quam_config")


def test_every_plan_gets_the_one_descriptor_and_no_spectator_rows(env, monkeypatch):
    _ready(env)
    seen = []
    real = cgb.plan

    def spy(*a, **kw):
        seen.append(kw)
        return real(*a, **kw)
    monkeypatch.setattr(cgb, "plan", spy)
    rows = {"q1-2": [{"qubit": "q3", "length": 32, "level": 0.1}]}
    j = env.c.post(PLAN, json=_slot_body(rows=rows)).get_json()
    assert _pair(j)["ok"], _pair(j)
    assert seen and all(callable((kw.get("gate_desc") or {}).get("skeleton")) for kw in seen)
    assert all("spectators" not in kw or kw["spectators"] is None for kw in seen)
    rows_out = _pair(j)["spectators"]
    assert rows_out[0]["qubit"] == "q3" and rows_out[0]["unit"] == 0.25
    assert rows_out[0]["reachable"] == sgt.reachable_level(0.25) and not rows_out[0]["unit_created"]


def test_the_check_reads_the_creates_and_names_the_unit_operation(env):
    _ready(env)
    rows = {"q1-2": [{"qubit": "q4", "length": 32, "level": 0.1, "unit": 0.2}]}
    j = _check(env, _slot_body(rows=rows))
    assert _pair(j)["verdict"]["allowed"], _pair(j)
    creates = env.fake.calls[-1]["creates"]
    unit_path = f"qubits.q4.z.operations.{UNIT}"
    assert [p for p, _ in creates].count(unit_path) == 1
    macro = next(v for p, v in creates if p == "qubit_pairs.q1-2.macros.cz_s")
    assert macro["__class__"] == QCLASS and macro[sgp.SPECTATOR_LEVELS] == {"q4": 0.1}


@pytest.fixture
def bare(monkeypatch):
    """A chip without any unit operation."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        yield Env(tmp, monkeypatch, _state(units={}))
        from quam_state_manager.core.hub_index import close_readers
        close_readers()


def test_one_unit_operation_per_qubit_for_the_whole_request(bare):
    _ready(bare)
    row = {"qubit": "q3", "length": 32, "level": 0.1, "unit": 0.2}
    rows = {"q1-2": [row], "q4-5": [dict(row)]}
    j = _check(bare, _slot_body(pairs=("q1-2", "q4-5"), rows=rows))
    assert all(p["verdict"]["allowed"] for p in j["pairs"]), j["pairs"]
    paths = [p for p, _ in bare.fake.calls[-1]["creates"]]
    assert paths.count(f"qubits.q3.z.operations.{UNIT}") == 1
    r = bare.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    assert r.status_code == 200, r.get_json()
    assert bare.a.store.merged["qubits"]["q3"]["z"]["operations"][UNIT]["amplitude"] == 0.2


def test_a_gate_refused_after_its_rows_leaves_no_unit_behind(bare, monkeypatch):
    _ready(bare)
    real = czr.class_files
    calls = []

    def first_fails(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise Q.ClassFileError("The class file cannot be read.")
        return real(*a, **kw)
    monkeypatch.setattr(czr, "class_files", first_fails)
    row = {"qubit": "q3", "length": 32, "level": 0.1, "unit": 0.2}
    j = bare.c.post(PLAN, json=_slot_body(pairs=("q1-2", "q4-5"),
                                          rows={"q1-2": [row], "q4-5": [dict(row)]})).get_json()
    assert _pair(j, "q1-2")["refusals"] == ["The class file cannot be read."]
    second = _pair(j, "q4-5")
    assert second["ok"], second
    assert f"qubits.q3.z.operations.{UNIT}" in second["paths"]


def test_a_blank_unit_is_passed_on_as_absent(env):
    _ready(env)
    for blank in ("", None):
        rows = {"q1-2": [{"qubit": "q3", "length": 32, "level": 0.1, "unit": blank}]}
        assert _pair(env.c.post(PLAN, json=_slot_body(rows=rows)).get_json())["ok"]


def test_row_refusals_are_passed_on_unchanged(env):
    _ready(env)
    for rows, want in (
            ({"q1-2": ["q3"]}, sgp.ROW_PROBLEM.format(value="q3")),
            ({"q1-2": [{"qubit": "q4", "length": 32, "level": 0.1}]},
             sgt.UNIT_PROBLEM.format(name="q4")),
            ({"q1-2": [{"qubit": "q3", "length": 32, "level": 0.1, "unit": 0.3}]},
             sgp.UNIT_GIVEN_PROBLEM.format(name="q3", have=0.25))):
        assert _pair(env.c.post(PLAN, json=_slot_body(rows=rows)).get_json())["refusals"] == [want]


def test_rows_for_a_class_without_spectators_get_the_descriptor_sentence(env):
    _ready(env, spectators=False)
    rows = {"q1-2": [{"qubit": "q3", "length": 32, "level": 0.1}]}
    j = env.c.post(PLAN, json=_slot_body(rows=rows)).get_json()
    assert _pair(j)["refusals"] == [cgb.NO_SPECTATORS_DESCRIBED]


def test_a_slot_class_is_never_known_to_may_create(env, monkeypatch):
    _ready(env)
    monkeypatch.setattr(czr, "class_files", lambda *a, **k: [])
    monkeypatch.setattr(czr, "run_check", _fake(status="no-env", checked=False))
    seen = []
    real = cz_gate_create.may_create

    def spy(*a, **kw):
        seen.append(kw)
        return real(*a, **kw)
    monkeypatch.setattr(cz_gate_create, "may_create", spy)
    j = _check(env, _slot_body())
    assert seen and seen[-1]["layout"] != "known"
    assert _pair(j)["verdict"]["allowed"] is False


def test_a_level_out_of_range_is_refused_alike_by_check_and_create(env):
    _ready(env)
    rows = {"q1-2": [{"qubit": "q3", "length": 32, "level": 0.5}]}
    want = sgt.LEVEL_PROBLEM.format(name="q3", value=0.5)
    before = env.stored()
    plan = _pair(env.c.post(PLAN, json=_slot_body(rows=rows)).get_json())
    assert want in plan["refusals"][0]
    j = _check(env, _slot_body(rows=rows))
    assert want in _pair(j)["verdict"]["reason"] and want in " ".join(_pair(j)["refusals"])
    r = env.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    assert r.status_code == 400 and want in r.get_json()["error"]
    assert env.stored() == before
    for text in (plan["refusals"][0], r.get_json()["error"]):
        assert not any(bad in text for bad in (" -- ", "->", "CAS", "patch"))


# ---------------------------------------------------------------- unchanged for a table class

def _table_answers(e):
    plan = e.c.post(PLAN, json=_body()).get_json()
    chk = _check(e, _body())
    chk.pop("check_token")
    return plan, chk


def test_a_table_class_answers_the_same_with_a_slot_draft_present(monkeypatch):
    out = []
    for with_draft in (False, True):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            e = Env(tmp, monkeypatch)
            if with_draft:
                _ready(e)
            plan, chk = _table_answers(e)
            j = _check(e, _body())
            r = e.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
            made = r.get_json()
            out.append((plan, chk, r.status_code, [c["paths"] for c in made["created"]],
                        e.a.store.merged["qubit_pairs"]))
            from quam_state_manager.core.hub_index import close_readers
            close_readers()
    assert out[0] == out[1]


# ---------------------------------------------------------------- the real lab env

def _lab_chip(out: Path) -> tuple[dict, dict]:
    """A chip the lab's code loads: built by the generator in the lab env with
    a quam_builder root, flux lines and fixed CZ pairs."""
    from tests.test_coupler_only_live import _build, _spec
    spec = _spec(quam_class=None)
    spec.pop("populate")
    spec["pair_gate"] = "cz_fixed"
    spec["lines"] = (spec["lines"] + [{"element": q, "line": "flux", "channel": {"kind": "lf_fem"}}
                        for q in spec["qubits"]])
    _build(spec, out)
    return (json.loads((out / "state.json").read_text(encoding="utf-8")),
            json.loads((out / "wiring.json").read_text(encoding="utf-8")))


def _wired_app(wiring: dict):
    """The test app class with the chip's own wiring in its live folder."""
    class WiredApp(App):
        def __init__(self, tmp, state=None):
            from quam_state_manager.web.app import create_app
            live = Path(tmp) / "chips" / "live"
            live.mkdir(parents=True)
            (live / "state.json").write_text(json.dumps(state), encoding="utf-8")
            (live / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
            self.app = create_app(testing=True, instance_path=str(Path(tmp) / "_inst"))
            self.c = self.app.test_client()
            assert self.c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return WiredApp


@pytest.mark.skipif(not LAB_PY.is_file(), reason="SM_LAB_ENV_PYTHON not set or absent")
def test_real_lab_saves_the_class_and_the_created_gate_passes_its_check(monkeypatch):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        st, wiring = _lab_chip(Path(tmp) / "built")
        monkeypatch.setattr(sys.modules[__name__], "App", _wired_app(wiring))
        e = Env(tmp, monkeypatch, st, real_lab=True)
        assert e.a.store.wiring == wiring
        monkeypatch.setenv("PYTHONPATH", str(e.folder.parent))   # the lab env imports the tmp folder
        monkeypatch.setattr(czr, "_env_python", lambda: str(LAB_PY))
        e.make(slots=["coupler"], spectators=False)
        e.save(python=str(LAB_PY))
        body = _slot_body(slots=["coupler"])
        j = _check(e, body)
        p = _pair(j)
        assert p["verdict"]["allowed"], json.dumps(p["checks"])[:3000]
        r = e.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
        assert r.status_code == 200, r.get_json()
        made = r.get_json()["created"][0]
        assert made["state"] == cz_gate_create.VERIFIED and made["record_error"] is None
        assert e.a.store.merged["qubit_pairs"]["q1-2"]["macros"]["cz_s"]["__class__"] == QCLASS
        from quam_state_manager.core.hub_index import close_readers
        close_readers()


# ---------------------------------------------------------------- a shared unit and the verdicts

def _failing(fails):
    """A check runner that fails one check of every pair in *fails*."""
    base = _fake()

    def run(store, python_path, gates, creates, previews):
        out = base(store, python_path, gates, creates, previews)
        for g in out["gates"]:
            if g["pair"] in fails:
                g["checks"]["range"] = {"status": "fail", "message": "The output range is exceeded."}
        return out
    run.calls = base.calls
    return run


UNIT_ROW = {"qubit": "q3", "length": 32, "level": 0.1, "unit": 0.2}
UNIT_PATH = f"qubits.q3.z.operations.{UNIT}"


def _shared_unit_run(tmp, monkeypatch, pairs, fails):
    """Check then create *pairs* that all scale a new unit on q3, with the
    check failing *fails*: ``(env, check answer, create response, written paths)``."""
    e = Env(tmp, monkeypatch, _state(n=7, units={}))
    _ready(e)
    monkeypatch.setattr(czr, "run_check", _failing(set(fails)))
    j = _check(e, _slot_body(pairs=pairs, rows={p: [dict(UNIT_ROW)] for p in pairs}))
    before = len(e.a.store.change_log)
    r = e.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    written = [x.dot_path for x in e.a.store.change_log[before:]]
    return e, j, r, written


@pytest.mark.parametrize("pairs,fails", [
    (("q1-2", "q4-5"), ("q1-2",)),
    (("q1-2", "q4-5"), ("q4-5",)),
    (("q1-2", "q4-5", "q6-7"), ("q4-5",)),
    (("q1-2", "q4-5", "q6-7"), ("q1-2", "q4-5"))])
def test_a_shared_unit_is_written_with_the_gates_that_are_written(tmp_path, monkeypatch, pairs, fails):
    e, j, r, written = _shared_unit_run(tmp_path, monkeypatch, pairs, fails)
    try:
        assert r.status_code == 200, r.get_json()
        made = [p for p in pairs if p not in fails]
        assert [c["pair"] for c in r.get_json()["created"]] == made
        merged = e.a.store.merged
        for pid in pairs:
            has = "cz_s" in (merged["qubit_pairs"][pid].get("macros") or {})
            assert has == (pid in made), pid
        assert merged["qubits"]["q3"]["z"]["operations"][UNIT]["amplitude"] == 0.2
        assert written.count(UNIT_PATH) == 1
        shown = [x for p in j["pairs"] if p["pair"] in made for x in p["paths"]]
        assert sorted(written) == sorted(shown) and UNIT_PATH in shown
        assert not any(f"qubit_pairs.{p}." in x for p in fails for x in written)
        # the stored check of each written gate read exactly what was written
        final = czr.run_check.calls[-1]["creates"]
        final_paths = [p for p, _ in final]
        assert set(written) <= set(final_paths) and final_paths.count(UNIT_PATH) == 1
        owner = next(x["pair"] for x in j["pairs"] if UNIT_PATH in x["paths"])
        assert owner == made[0]
        assert all(UNIT_PATH not in x["paths"] for x in j["pairs"] if x["pair"] in fails)
        assert [p["paths"].count(UNIT_PATH) for p in j["pairs"] if p["pair"] in made] == \
            [1] + [0] * (len(made) - 1)
    finally:
        from quam_state_manager.core.hub_index import close_readers
        close_readers()


def test_a_gate_refused_only_once_it_owns_the_unit_ends_refused_and_the_unit_moves_on(tmp_path, monkeypatch):
    pairs = ("q1-2", "q4-5", "q6-7")
    base = _fake()

    def run(store, python_path, gates, creates, previews):
        out = base(store, python_path, gates, creates, previews)
        for g in out["gates"]:
            first = gates[0][0] == "q4-5" and g["pair"] == "q4-5"   # fails only as the unit's owner
            if g["pair"] == "q1-2" or first:
                g["checks"]["range"] = {"status": "fail", "message": "The output range is exceeded."}
        return out
    run.calls = base.calls
    e = Env(tmp_path, monkeypatch, _state(n=7, units={}))
    try:
        _ready(e)
        monkeypatch.setattr(czr, "run_check", run)
        j = _check(e, _slot_body(pairs=pairs, rows={p: [dict(UNIT_ROW)] for p in pairs}))
        allowed = {x["pair"]: x["verdict"]["allowed"] for x in j["pairs"]}
        assert allowed == {"q1-2": False, "q4-5": False, "q6-7": True}
        held = {x["pair"]: x["paths"].count(UNIT_PATH) for x in j["pairs"]}
        assert held["q6-7"] == 1 and held["q1-2"] == 0 and held["q4-5"] == 0
        before = len(e.a.store.change_log)
        r = e.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
        assert r.status_code == 200, r.get_json()
        assert [c["pair"] for c in r.get_json()["created"]] == ["q6-7"]
        written = [x.dot_path for x in e.a.store.change_log[before:]]
        merged = e.a.store.merged
        for pid in pairs:
            assert ("cz_s" in (merged["qubit_pairs"][pid].get("macros") or {})) == (pid == "q6-7")
        assert merged["qubits"]["q3"]["z"]["operations"][UNIT]["amplitude"] == 0.2
        assert written.count(UNIT_PATH) == 1
        assert not any(f"qubit_pairs.{p}." in x for p in ("q1-2", "q4-5") for x in written)
        final = czr.run_check.calls[-1]
        assert [g[0] for g in final["gates"]] == ["q6-7"]
        assert sorted(written) == sorted(p for p, _ in final["creates"])
    finally:
        from quam_state_manager.core.hub_index import close_readers
        close_readers()


def test_no_unit_is_written_when_every_gate_needing_it_is_refused(tmp_path, monkeypatch):
    pairs = ("q1-2", "q4-5")
    e, j, r, written = _shared_unit_run(tmp_path, monkeypatch, pairs, pairs)
    try:
        assert r.status_code == 400 and written == []
        assert UNIT not in e.a.store.merged["qubits"]["q3"]["z"]["operations"]
        assert all(UNIT_PATH not in x["paths"] for x in j["pairs"])
    finally:
        from quam_state_manager.core.hub_index import close_readers
        close_readers()


def test_assign_units_gives_a_unit_to_the_first_written_gate_only():
    unit = (UNIT_PATH, {"id": UNIT, "amplitude": 0.2})
    rows = [{"qubit": "q3", "unit_operation": UNIT_PATH, "unit_created": True}]
    plans = [("a", {"creates": [("m.a", {}), unit], "spectators": rows}),
             ("b", {"creates": [("m.b", {})],
                    "spectators": [dict(rows[0], unit_created=False)]}),
             ("c", {"error": "no"})]
    got = dict(czr.assign_units(plans, {"b"}))
    assert got["a"]["creates"] == [("m.a", {})] and not got["a"]["spectators"][0]["unit_created"]
    assert got["b"]["creates"] == [("m.b", {}), unit] and got["b"]["spectators"][0]["unit_created"]
    assert dict(czr.assign_units(plans, set()))["b"]["creates"] == [("m.b", {})]
    assert plans[0][1]["creates"] == [("m.a", {}), unit]   # never changed in place


# ---------------------------------------------------------------- row shape and small pins

def test_every_planned_slot_row_carries_the_not_calibrated_line(env):
    _ready(env)
    rows = {"q1-2": [{"qubit": "q3", "length": 32, "level": 0.1},
                     {"qubit": "q4", "length": 32, "level": 0.1, "unit": 0.2}]}
    p = _pair(env.c.post(PLAN, json=_slot_body(rows=rows)).get_json())
    assert [r["not_calibrated"] for r in p["spectators"]] == [cgb.NOT_CALIBRATED] * 2
    assert p["spectator_ops"] == {"q3": 1, "q4": 1}


def test_a_stored_unit_that_is_not_a_number_is_no_unit(monkeypatch):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        e = Env(tmp, monkeypatch, _state(units={"q3": "#/qubits/q4/z/operations/const/amplitude",
                                                "q4": "text"}))
        e.make()
        rows = {r["qubit"]: r for r in next(p for p in e.dialog()["pairs"]
                                            if p["id"] == "q1-2")["spectator_candidates"]}
        assert rows["q3"]["unit"] is None and rows["q4"]["unit"] is None
        assert rows["q3"]["reachable"] is None and rows["q4"]["reachable"] is None
        from quam_state_manager.core.hub_index import close_readers
        close_readers()


def test_a_gate_of_a_slot_class_lists_the_class_once(env):
    _ready(env)
    j = _check(env, _slot_body())
    r = env.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    assert r.status_code == 200, r.get_json()
    listed = [c for c in env.dialog()["classes"] if c["class"] == QCLASS]
    assert [c["layout"] for c in listed] == ["slot"]


def test_the_slot_route_adopts_the_draft_it_wrote(env):
    env.make()
    w = W.watch_for(env.a.app.instance_path, env.scope, NAME)   # watching the first draft
    env.make(spectators=False)                                   # replaced while no gate uses it
    assert W.watch_for(env.a.app.instance_path, env.scope, NAME) is w
    src = sgt.render(sgt.SlotSpec(NAME, list(sgt.SLOTS), False))
    assert w.sha == D.sha256_bytes(src.encode("utf-8"))
    assert w.poll()["status"] == "unchanged"


def test_slot_spec_says_whether_the_class_has_spectators(env):
    env.make(spectators=False)
    c = next(c for c in env.dialog()["classes"] if c["layout"] == "slot")
    assert c["slot_spec"]["spectators"] is False
