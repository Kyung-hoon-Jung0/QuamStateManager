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


def _state(n=5, units=None, xy=("x180", "x90")):
    st = _chip(n)
    for q in st["qubits"].values():      # an xy channel with the operations a step may name
        q["xy"] = {"operations": {op: {"length": 40, "amplitude": 0.1} for op in xy}}
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
                      "slot_spec", "spectators", "older_render"}
    assert c["label"] == f"{NAME} (slot class SM made)" and c["available"] and c["why"] == ""
    assert c["slot_spec"] == {"slots": list(sgt.SLOTS), "spectators": True, "before": [], "after": []}
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
    row = _pair(j)
    assert row["refusals"][0].startswith(f"Save {NAME} to quam_config")
    # the class to save first, named apart from the sentence so the dialog can offer its page
    assert row["save_class"] == {"cls": NAME, "refusal": row["refusals"][0]}


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


# ---------------------------------------------------------------- older renders

OWN = ("control", "target")


def _older_draft(env, slots=OWN, spectators=True, name=NAME):
    text = sgt.render_v1(sgt.SlotSpec(name, frozenset(slots), spectators))
    D.save_draft(env.a.app.instance_path, env.scope, name, text)
    W.watch_for(env.a.app.instance_path, env.scope, name).adopt(text.encode("utf-8"))
    return text


def test_an_older_render_draft_is_listed_and_made_again_as_the_current_render(env):
    _older_draft(env)
    c = next(c for c in env.dialog()["classes"] if c["layout"] == "slot")
    assert c["class"] == QCLASS and c["older_render"] is True
    env.make(slots=OWN)
    text = D.load_draft(env.a.app.instance_path, env.scope, NAME).decode("utf-8")
    assert text == sgt.render(sgt.SlotSpec(NAME, frozenset(OWN), True))
    assert sgt.parse(text).version == sgt.TEMPLATE_VERSION
    assert next(c for c in env.dialog()["classes"] if c["layout"] == "slot")["older_render"] is False


def test_junk_under_the_name_still_gets_the_draft_refusal(env):
    D.save_draft(env.a.app.instance_path, env.scope, NAME, _older_draft(env) + "# edited\n")
    assert env.make(slots=OWN, code=409)["error"] == czr.NAME_DRAFT.format(name=NAME)


def test_an_older_render_in_use_is_made_again_only_with_its_own_spec(env):
    _older_draft(env)
    env.save()
    j = _check(env, _slot_body(slots=OWN))
    assert env.c.post(CREATE, json={"check_token": j["check_token"],
                                    "plan_sig": j["plan_sig"]}).status_code == 200
    assert env.make(slots=["coupler"], code=409)["error"] == czr.NAME_IN_USE.format(name=NAME, count=1)
    env.make(slots=OWN)
    assert sgt.parse(D.load_draft(env.a.app.instance_path, env.scope, NAME).decode()).version == 2


def _notes(env, slots, gate="cz_s"):
    plan = _pair(env.c.post(PLAN, json=_slot_body(slots=slots, name=gate)).get_json())
    check = _pair(_check(env, _slot_body(slots=slots, name=gate)))
    assert plan["ok"] and check["ok"], (plan, check)
    return plan["notes"], check["notes"]


def test_a_gate_on_an_older_control_or_target_lab_file_says_so(env):
    _older_draft(env)
    env.save()
    note = czr.OLDER_RENDER_NOTE.format(file=f"{NAME}.py", name=NAME)
    assert _notes(env, OWN) == ([note], [note])
    env.make(slots=OWN)
    env.save()
    assert _notes(env, OWN) == ([], [])


def test_the_build_notes_stay_first_and_the_older_render_note_follows(env, monkeypatch):
    _older_draft(env)
    env.save()
    real = cgb.plan

    def with_note(*a, **kw):
        out = real(*a, **kw)
        out["notes"] = ["first build note", "second build note"]
        return out
    monkeypatch.setattr(cgb, "plan", with_note)
    note = czr.OLDER_RENDER_NOTE.format(file=f"{NAME}.py", name=NAME)
    plan = _pair(env.c.post(PLAN, json=_slot_body(slots=OWN, name="cz_s")).get_json())
    assert plan["ok"], plan
    assert plan["notes"] == ["first build note", "second build note", note]


def test_a_gate_on_an_older_coupler_only_lab_file_says_nothing(env):
    _older_draft(env, slots=["coupler"])
    env.save()
    assert _notes(env, ["coupler"]) == ([], [])


STEPS = ([{"kind": "wait", "length": 16}], [{"kind": "xy", "qubit": "control", "operation": "x180"}])


def _stepped_draft(env, render, slots=OWN, spectators=True):
    spec = sgt.SlotSpec(NAME, frozenset(slots), spectators, *STEPS)
    text = render(spec)
    D.save_draft(env.a.app.instance_path, env.scope, NAME, text)
    W.watch_for(env.a.app.instance_path, env.scope, NAME).adopt(text.encode("utf-8"))
    env.save()
    j = _check(env, _slot_body(slots=slots))
    assert env.c.post(CREATE, json={"check_token": j["check_token"],
                                    "plan_sig": j["plan_sig"]}).status_code == 200
    return spec


def _draft_text(env):
    return D.load_draft(env.a.app.instance_path, env.scope, NAME).decode("utf-8")


def test_a_stepped_older_render_in_use_is_made_again_with_its_steps(env):
    spec = _stepped_draft(env, sgt.render_v1)
    j = env.make(slots=OWN)
    assert j["ok"] and j["cls"] == NAME
    text = _draft_text(env)
    assert text == sgt.render(spec)
    parsed = sgt.parse(text)
    assert parsed.version == sgt.TEMPLATE_VERSION and parsed.spec == spec
    assert parsed.spec.before == spec.before and parsed.spec.after == spec.after


@pytest.mark.parametrize("change", [{"slots": ["control"]}, {"spectators": False}])
def test_a_stepped_older_render_in_use_with_another_carried_field_is_refused(env, change):
    _stepped_draft(env, sgt.render_v1)
    before = env.stored()
    j = env.make(**{"slots": OWN, **change}, code=409)
    assert j["error"] == czr.NAME_IN_USE.format(name=NAME, count=1)
    assert env.stored() == before


def test_other_posted_steps_on_an_in_use_older_render_are_name_in_use(env):
    # another step list is another class body: the in-use rule answers, nothing is written
    _stepped_draft(env, sgt.render_v1)
    before = env.stored()
    r = env.c.post(SLOT_ROUTE, json={"class_name": NAME, "slots": list(OWN), "spectators": True,
                                     "before": [{"kind": "wait", "length": 32}], "after": []})
    assert r.status_code == 409
    assert r.get_json()["error"] == czr.NAME_IN_USE.format(name=NAME, count=1)
    assert env.stored() == before


def test_the_stored_steps_posted_again_remake_the_in_use_older_render(env):
    spec = _stepped_draft(env, sgt.render_v1)
    r = env.c.post(SLOT_ROUTE, json={"class_name": NAME, "slots": list(OWN), "spectators": True,
                                     "before": list(STEPS[0]), "after": list(STEPS[1])})
    assert r.status_code == 200 and r.get_json()["ok"], r.get_json()
    assert _draft_text(env) == sgt.render(spec)


def test_a_stepped_current_render_in_use_is_still_refused(env):
    _stepped_draft(env, sgt.render)
    before = env.stored()
    assert env.make(slots=OWN, code=409)["error"] == czr.NAME_IN_USE.format(name=NAME, count=1)
    assert env.stored() == before


def _mtimes(env):
    return {str(p.relative_to(env.tmp)): p.stat().st_mtime_ns
            for p in sorted(env.tmp.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}


def _in_use_older_made_again(env):
    _older_draft(env)
    env.save()
    j = _check(env, _slot_body(slots=OWN))
    assert env.c.post(CREATE, json={"check_token": j["check_token"],
                                    "plan_sig": j["plan_sig"]}).status_code == 200
    env.make(slots=OWN)
    assert sgt.parse(_draft_text(env)).version == sgt.TEMPLATE_VERSION


def test_making_an_in_use_class_again_with_the_same_text_writes_nothing_and_the_note_names_the_save(env):
    _in_use_older_made_again(env)
    before, times = env.stored(), _mtimes(env)
    j = env.make(slots=OWN)
    assert j == {"ok": True, "cls": NAME, "qclass": QCLASS}
    assert env.stored() == before and _mtimes(env) == times
    note = czr.OLDER_RENDER_SAVE_NOTE.format(file=f"{NAME}.py", name=NAME)
    assert _notes(env, OWN, "cz_t") == ([note], [note])
    env.save()
    assert _notes(env, OWN, "cz_t") == ([], [])


@pytest.mark.parametrize("change", [{"slots": ["control"]}, {"spectators": False}])
def test_a_changed_make_of_an_in_use_current_render_is_refused(env, change):
    _in_use_older_made_again(env)
    before = env.stored()
    j = env.make(**{"slots": OWN, **change}, code=409)
    assert j["error"] == czr.NAME_IN_USE.format(name=NAME, count=1)
    assert env.stored() == before


def test_posted_steps_on_an_in_use_current_render_are_name_in_use(env):
    _in_use_older_made_again(env)
    before = env.stored()
    r = env.c.post(SLOT_ROUTE, json={"class_name": NAME, "slots": list(OWN), "spectators": True,
                                     "before": [{"kind": "wait", "length": 32}], "after": []})
    assert r.status_code == 409
    assert r.get_json()["error"] == czr.NAME_IN_USE.format(name=NAME, count=1)
    assert env.stored() == before


_WAIT = [{"kind": "wait", "length": 32}]
_GOOD = {"before": [{"kind": "align"}, {"kind": "wait", "length": 24},
                    {"kind": "xy", "qubit": "target", "operation": "x90"}],
         "after": [{"kind": "xy", "qubit": "control", "operation": "x180"}, {"kind": "align"}]}


def test_accepted_steps_are_written_and_read_back_value_by_value(env):
    r = env.c.post(SLOT_ROUTE, json={"class_name": NAME, "slots": list(OWN), "spectators": True, **_GOOD})
    assert r.status_code == 200 and r.get_json()["ok"], r.get_json()
    parsed = sgt.parse(_draft_text(env))
    assert [dict(x) for x in parsed.spec.before] == _GOOD["before"]
    assert [dict(x) for x in parsed.spec.after] == _GOOD["after"]
    assert _draft_text(env) == sgt.render(sgt.SlotSpec(NAME, list(OWN), True, _GOOD["before"], _GOOD["after"]))
    listed = next(c for c in env.dialog()["classes"]
                  if c["class"] == QCLASS)["slot_spec"]
    assert listed["before"] == _GOOD["before"] and listed["after"] == _GOOD["after"]


_BAD = [  # (steps, where, index of the named row or None)
    ({"before": [{"kind": "align"}, {"kind": "hop"}]}, "before", 1),
    ({"after": [{"kind": "wait", "length": -4}]}, "after", 0),
    ({"after": [{"kind": "wait", "length": 16}, {"kind": "xy", "qubit": "middle", "operation": "x180"}]}, "after", 1),
    ({"before": [{"kind": "xy", "qubit": "control", "operation": "not a name"}]}, "before", 0),
    ({"before": [{"kind": "align", "length": 4}]}, "before", 0),
    ({"before": [7]}, "before", 0),
    ({"after": "wait"}, None, None), ({"before": 1}, None, None), ({"before": {"kind": "wait"}}, None, None),
    ({"before": 0}, None, None), ({"after": False}, None, None), ({"before": ""}, None, None),
    ({"after": {}}, None, None)]


@pytest.mark.parametrize("steps, where, index", _BAD)
def test_refused_steps_say_the_one_check_sentence_at_its_row_and_write_nothing(env, steps, where, index):
    before = env.stored()
    r = env.c.post(SLOT_ROUTE, json={"class_name": NAME, "slots": list(OWN), "spectators": True, **steps})
    assert r.status_code == 400
    j = r.get_json()
    spec = sgt.SlotSpec(NAME, list(OWN), True, steps.get("before", ()), steps.get("after", ()))
    assert j["ok"] is False and j["problems"] == sgt.validate(spec) and j["error"] == j["problems"][0]
    assert j["error"] in {sgt.sequence_steps_problem(steps.get(w, ()), w) for w in ("before", "after")}
    assert j.get("step") == ({"where": where, "index": index} if where else None)
    assert env.stored() == before
    assert D.load_draft(env.a.app.instance_path, env.scope, NAME) is None


def test_posted_steps_on_a_stored_free_draft_replace_it(env):
    # a name not in use: the draft is replaced, as for any other changed body
    env.make(slots=OWN)
    r = env.c.post(SLOT_ROUTE, json={"class_name": NAME, "slots": ["control"], "spectators": True,
                                     "after": _WAIT})
    assert r.status_code == 200 and r.get_json()["ok"]
    assert _draft_text(env) == sgt.render(sgt.SlotSpec(NAME, ["control"], True, (), _WAIT))


def test_refused_steps_on_a_stored_free_draft_leave_its_bytes(env):
    env.make(slots=OWN)
    before, text = env.stored(), _draft_text(env)
    r = env.c.post(SLOT_ROUTE, json={"class_name": NAME, "slots": ["control"], "spectators": True,
                                     "after": [{"kind": "wait", "length": "x y"}]})
    assert r.status_code == 400
    assert env.stored() == before and _draft_text(env) == text


def test_an_xy_step_the_qubit_lacks_is_refused_by_the_plan_and_nothing_is_written(env):
    steps = {"after": [{"kind": "wait", "length": 16}, {"kind": "xy", "qubit": "target", "operation": "x45"}]}
    r = env.c.post(SLOT_ROUTE, json={"class_name": NAME, "slots": list(OWN), "spectators": True, **steps})
    assert r.status_code == 200
    env.save()
    before = env.stored()
    pair = _pair(env.c.post(PLAN, json=_slot_body(slots=OWN)).get_json())
    want = sgt.XY_MISSING_PROBLEM.format(number=2, where="after", role="target", operation="x45")
    assert pair["ok"] is False and pair["refusals"] == [want], pair
    check = _pair(_check(env, _slot_body(slots=OWN)))
    assert not (check.get("verdict") or {}).get("allowed")
    assert want in json.dumps(check)
    assert env.stored() == before


def test_a_gate_with_steps_says_its_steps_in_the_plan(env):
    r = env.c.post(SLOT_ROUTE, json={"class_name": NAME, "slots": list(OWN), "spectators": True, **_GOOD})
    assert r.status_code == 200
    env.save()
    pair = _pair(env.c.post(PLAN, json=_slot_body(slots=OWN)).get_json())
    spec = sgt.SlotSpec(NAME, list(OWN), True, _GOOD["before"], _GOOD["after"])
    assert pair["ok"] and sgp.steps_sentence(spec) in pair["notes"]
    assert sgp.steps_sentence(sgt.SlotSpec(NAME, list(OWN), True)) is None


@pytest.mark.parametrize("steps", [{}, {"before": None}, {"before": [], "after": []}, {"after": None}])
def test_empty_steps_make_the_class_as_before(env, steps):
    r = env.c.post(SLOT_ROUTE, json={"class_name": NAME, "slots": list(OWN), "spectators": True, **steps})
    assert r.status_code == 200 and r.get_json()["ok"]
    assert _draft_text(env) == sgt.render(sgt.SlotSpec(NAME, list(OWN), True))


@pytest.mark.parametrize("body, error", [({"class_name": 3, "slots": list(OWN)}, "name"),
                                         ({"class_name": NAME, "slots": "nope"}, "slots")])
def test_a_bad_name_or_slots_is_refused_before_posted_steps(env, body, error):
    r = env.c.post(SLOT_ROUTE, json={**body, "before": _WAIT})
    assert r.status_code == 400
    got = r.get_json()["error"]
    if error == "name":
        assert got == czr.NAME_NOT_TEXT
    else:
        assert got == sgt.validate(sgt.SlotSpec(NAME, "nope", False))[0]
        assert "step" not in r.get_json()


# ------------------------------------------------ roles, fixed couplers, notes

def _dialog_pair(data, pid):
    return next(p for p in data["pairs"] if p["id"] == pid)


def _role_state():
    """A chip whose control (q1) and target (q2) qubits carry different xy operations."""
    st = _state()
    st["qubits"]["q1"]["xy"] = {"operations": {"shared_op": {"length": 40, "amplitude": 0.1},
                                               "ctl_only": {"length": 40, "amplitude": 0.1}}}
    st["qubits"]["q2"]["xy"] = {"operations": {"tgt_only": {"length": 40, "amplitude": 0.1}}}
    return st


@pytest.fixture
def role_env(monkeypatch):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        yield Env(tmp, monkeypatch, state=_role_state())
        from quam_state_manager.core.hub_index import close_readers
        close_readers()


def test_each_role_offers_only_its_own_qubits_xy_operations(role_env):
    ops = _dialog_pair(role_env.dialog(), "q1-2")["xy_operations"]
    assert ops == {"control": ["ctl_only", "shared_op"], "target": ["tgt_only"]}
    merged = _role_state()
    assert sgp.xy_operations(merged, "q1-2", "control") == ["ctl_only", "shared_op"]
    assert sgp.xy_operations(merged, "q1-2", "target") == ["tgt_only"]


@pytest.mark.parametrize("where", ["before", "after"])
@pytest.mark.parametrize("role, operation, number", [("control", "tgt_only", 1), ("target", "ctl_only", 2)])
def test_a_step_naming_the_other_qubits_operation_is_refused_with_step_role_and_operation(
        role_env, role, operation, number, where):
    steps = [{"kind": "wait", "length": 16}][:number - 1] + [{"kind": "xy", "qubit": role, "operation": operation}]
    r = role_env.c.post(SLOT_ROUTE, json={"class_name": NAME, "slots": list(OWN), "spectators": True,
                                          where: steps})
    assert r.status_code == 200
    role_env.save()
    pair = _pair(role_env.c.post(PLAN, json=_slot_body(slots=OWN)).get_json())
    want = sgt.XY_MISSING_PROBLEM.format(number=number, where=where, role=role, operation=operation)
    assert pair["ok"] is False and pair["refusals"] == [want], pair
    assert f"{role} qubit" in want and repr(operation) in want and f"Sequence step {number} {where}" in want


def test_each_role_accepts_its_own_operation_in_the_plan(role_env):
    steps = {"before": [{"kind": "xy", "qubit": "control", "operation": "ctl_only"},
                        {"kind": "xy", "qubit": "target", "operation": "tgt_only"}]}
    assert role_env.c.post(SLOT_ROUTE, json={"class_name": NAME, "slots": list(OWN),
                                             "spectators": True, **steps}).status_code == 200
    role_env.save()
    assert _pair(role_env.c.post(PLAN, json=_slot_body(slots=OWN)).get_json())["ok"]


def test_an_xy_entry_given_as_a_reference_is_followed_one_hop(role_env):
    st = _role_state()
    st["shared_xy"] = {"operations": {"via_ref": {"length": 40, "amplitude": 0.1}}}
    st["qubits"]["q2"]["xy"] = "#/shared_xy"
    assert sgp.xy_operations(st, "q1-2", "target") == ["via_ref"]
    assert sgp.xy_operations(st, "q1-2", "control") == ["ctl_only", "shared_op"]
    st["qubits"]["q2"]["xy"] = "#/shared_xy/missing"
    assert sgp.xy_operations(st, "q1-2", "target") is None


def test_the_steps_sentence_reads_before_and_after_words_apart():
    spec = sgt.SlotSpec(NAME, list(OWN), True, _GOOD["before"], _GOOD["after"])
    assert sgp.steps_sentence(spec) == (
        "This class plays steps before the gate pulses: align, wait 24 ns, play x90 on the target xy channel;"
        " after them: play x180 on the control xy channel, align.")
    only_after = sgt.SlotSpec(NAME, list(OWN), True, (), _WAIT)
    assert sgp.steps_sentence(only_after) == (
        "This class plays steps before the gate pulses: nothing; after them: wait 32 ns.")


def test_the_steps_note_comes_before_the_older_render_note_after_the_build_notes(env, monkeypatch):
    text = sgt.render_v1(sgt.SlotSpec(NAME, frozenset(OWN), True, *STEPS))
    D.save_draft(env.a.app.instance_path, env.scope, NAME, text)
    W.watch_for(env.a.app.instance_path, env.scope, NAME).adopt(text.encode("utf-8"))
    env.save()
    real = cgb.plan

    def with_note(*a, **kw):
        out = real(*a, **kw)
        out["notes"] = ["first build note"]
        return out
    monkeypatch.setattr(cgb, "plan", with_note)
    older = czr.OLDER_RENDER_NOTE.format(file=f"{NAME}.py", name=NAME)
    steps = ("This class plays steps before the gate pulses: wait 16 ns;"
             " after them: play x180 on the control xy channel.")
    plan = _pair(env.c.post(PLAN, json=_slot_body(slots=OWN, name="cz_s")).get_json())
    assert plan["ok"], plan
    assert plan["notes"] == ["first build note", steps, older]


def _fixed_coupler_state():
    st = _state()
    del st["qubit_pairs"]["q2-3"]["coupler"]
    return st


@pytest.fixture
def fixed_env(monkeypatch):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        yield Env(tmp, monkeypatch, state=_fixed_coupler_state())
        from quam_state_manager.core.hub_index import close_readers
        close_readers()


def test_the_dialog_offers_no_coupler_line_on_a_pair_without_one(fixed_env):
    data = fixed_env.dialog()
    assert _dialog_pair(data, "q2-3")["lines"] == {"control": True, "target": True, "coupler": False}
    assert _dialog_pair(data, "q1-2")["lines"] == {"control": True, "target": True, "coupler": True}


# ------------------------------------------------ the make door knows the pair it was opened on

def _no_draft(env):
    return D.load_draft(env.a.app.instance_path, env.scope, NAME) is None


@pytest.mark.parametrize("where", ["before", "after"])
def test_an_xy_step_the_pairs_qubit_lacks_is_refused_at_its_row_when_the_pair_is_posted(role_env, where):
    steps = [{"kind": "wait", "length": 16}, {"kind": "xy", "qubit": "target", "operation": "ctl_only"}]
    stored = role_env.stored()
    r = role_env.c.post(SLOT_ROUTE, json={"class_name": NAME, "slots": list(OWN), "spectators": True,
                                          "pair": "q1-2", where: steps})
    want = sgt.XY_MISSING_PROBLEM.format(number=2, where=where, role="target", operation="ctl_only")
    j = r.get_json()
    assert r.status_code == 400 and j["error"] == want and j["problems"] == [want]
    assert j["step"] == {"where": where, "index": 1}
    assert role_env.stored() == stored and _no_draft(role_env)
    # the same body without a pair keeps the present behaviour: made, refused only at use
    r = role_env.c.post(SLOT_ROUTE, json={"class_name": NAME, "slots": list(OWN), "spectators": True,
                                          where: steps})
    assert r.status_code == 200 and not _no_draft(role_env)


def test_an_xy_step_the_pairs_qubit_has_is_made_when_the_pair_is_posted(role_env):
    steps = {"before": [{"kind": "xy", "qubit": "control", "operation": "ctl_only"}],
             "after": [{"kind": "xy", "qubit": "target", "operation": "tgt_only"}]}
    r = role_env.c.post(SLOT_ROUTE, json={"class_name": NAME, "slots": list(OWN), "spectators": True,
                                          "pair": "q1-2", **steps})
    assert r.status_code == 200 and r.get_json()["ok"]
    assert _draft_text(role_env) == sgt.render(
        sgt.SlotSpec(NAME, list(OWN), True, steps["before"], steps["after"]))


def test_a_posted_pair_the_chip_lacks_is_refused_and_nothing_is_written(role_env):
    stored = role_env.stored()
    r = role_env.c.post(SLOT_ROUTE, json={"class_name": NAME, "slots": list(OWN), "spectators": True,
                                          "pair": "no-such-pair", "after": _WAIT})
    assert r.status_code == 400
    assert r.get_json()["error"] == czr.PAIR_UNKNOWN.format(pair="no-such-pair")
    assert role_env.stored() == stored and _no_draft(role_env)


# ------------------------------------------------ the class page reads back SM's own slot class

def test_the_class_page_reads_an_sm_made_slot_class_with_its_steps_as_sm_made():
    spec = sgt.SlotSpec(NAME, list(OWN), True, _GOOD["before"], _GOOD["after"])
    info = W._describe(sgt.render(spec))
    assert info["editable"] is False and "changed by hand" not in info["reason"]
    assert info["slot"]["before"] == list(_GOOD["before"]) and info["slot"]["after"] == list(_GOOD["after"])
    assert "align, wait 24 ns, play x90 on the target xy channel" in info["reason"]
    # an older render without steps still reads as SM-made
    old = W._describe(sgt.render_v1(sgt.SlotSpec(NAME, list(OWN), True)))
    assert old["slot"]["before"] == [] and "changed by hand" not in old["reason"]
    # a slot class edited by hand is not claimed as SM-made
    hand = W._describe(sgt.render(spec).replace("align(*channels)", "align()", 1))
    assert "slot" not in hand and "changed by hand" in hand["reason"]
