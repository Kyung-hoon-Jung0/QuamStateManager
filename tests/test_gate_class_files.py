"""A gate that uses SM-made classes records their files in its extras at
create, its status line compares those records with the published files,
the chip keeps the notice that it needs the file, and Re-check records a
changed file after the lab check passes again."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from quam_state_manager.core import chip_copy, gate_check
from quam_state_manager.core import gate_status as GS
from quam_state_manager.core import pulse_class_drafts as D
from quam_state_manager.core import pulse_class_watch as W
from quam_state_manager.core import quam_config_save as Q
from quam_state_manager.web import cz_gate_routes as czr
from tests.test_cz_gate_routes import CHECK, CREATE, _fake
from tests.test_quam_config_save_routes import CLASS, QCLASS, _save, _sm_class_body, env  # noqa: F401

PAIR, GATE = "q1-2", "cz_sq"
MPATH = f"qubit_pairs.{PAIR}.macros.{GATE}"
NOTICE = (f"this chip now needs quam_config/{CLASS}.py; every computer that opens this "
          "state needs the same file")


# ------------------------------------------------------------ status, unit level

class _Store:
    def __init__(self, macro):
        self.merged = {"qubit_pairs": {"p": {"macros": {"g": macro}}}}


def _macro(files=None, cls="quam_config.Slot.Slot"):
    m = {"__class__": cls}
    if files is not None:
        m["extras"] = {"sm_class_files": files}
    return m


def _entry(path: Path, proportional=True, data=b"class A: pass\n"):
    path.write_bytes(data)
    return {"qclass": QCLASS, "file": path.name, "sha256": hashlib.sha256(data).hexdigest(),
            "proportional": proportional}


def _items(macro, where=None):
    out = GS.status(_Store(macro), None, "p", "g", class_path=where)
    return {i["key"]: i for i in out if i["key"] != "needs"}, [i for i in out if i["key"] == "needs"]


def test_the_read_helper_gives_the_recorded_files_or_nothing():
    assert GS.sm_class_files(_macro()) == []
    assert GS.sm_class_files({"extras": {"other": 1}}) == []
    assert GS.sm_class_files({"extras": {"sm_class_files": "x"}}) == []
    rec = {"qclass": QCLASS, "file": "A.py", "sha256": "ab", "proportional": None}
    assert GS.sm_class_files(_macro([rec, 3])) == [rec]


def test_class_file_present_is_a_detail_and_its_hash_is_the_file_bytes(tmp_path):
    f = tmp_path / f"{CLASS}.py"
    e = _entry(f)
    items, needs = _items(_macro([e]), lambda q: str(f))
    assert items["class"]["text"] == "class file present" and items["class"]["pill"] is False
    assert [n["text"] for n in needs] == [NOTICE]
    # one changed byte in the file is "changed", not "present"
    f.write_bytes(f.read_bytes() + b"#")
    items, _ = _items(_macro([e]), lambda q: str(f))
    assert items["class"]["text"] == "class file changed since the gate was checked"
    assert items["class"]["pill"] is True and items["class"]["tone"] == "warn"


def test_class_file_missing_names_the_file(tmp_path):
    f = tmp_path / f"{CLASS}.py"
    e = _entry(f)
    f.unlink()
    items, needs = _items(_macro([e]), lambda q: str(f))
    assert items["class"]["text"] == f"class file missing: quam_config/{CLASS}.py"
    assert items["class"]["pill"] is True
    assert [n["text"] for n in needs] == [NOTICE]
    items, _ = _items(_macro([e]), None)          # no record of where it was saved
    assert items["class"]["text"] == f"class file missing: quam_config/{CLASS}.py"


def test_a_gate_without_sm_classes_says_not_a_class_sm_made():
    items, needs = _items(_macro(), lambda q: None)
    assert items["class"]["text"] == "not a class SM made" and items["class"]["pill"] is False
    assert items["scaling"]["text"] == "amplitude scaling: not known"
    assert items["scaling"]["pill"] is False and needs == []


@pytest.mark.parametrize("flag,text,pill", [
    (True, "amplitude scaling: checked", True),
    (False, "amplitude scaling: not proportional", True),
    (None, "amplitude scaling: not known", False)])
def test_amplitude_scaling_follows_the_recorded_result(tmp_path, flag, text, pill):
    f = tmp_path / f"{CLASS}.py"
    items, _ = _items(_macro([_entry(f, proportional=flag)]), lambda q: str(f))
    assert items["scaling"]["text"] == text and items["scaling"]["pill"] is pill


def test_class_files_copy_the_publish_receipt(tmp_path):
    inst, scope = tmp_path / "inst", "chip"
    folder = D.draft_path(inst, scope, CLASS).parent
    folder.mkdir(parents=True)
    receipt = {"path": str(tmp_path / f"{CLASS}.py"), "sha": "abc", "importable": True,
               "qclass": QCLASS, "proportional": {"proportional": False, "ratio_min": 1}}
    (folder / W.PUBLISHED_FILE).write_text(json.dumps(receipt), encoding="utf-8")
    assert Q.class_files(inst, scope, classes=[QCLASS]) == [
        {"qclass": QCLASS, "file": f"{CLASS}.py", "sha256": "abc", "proportional": False}]
    assert Q.class_files(inst, scope, classes=["quam.Other"]) == []
    assert Q.published_path(inst, scope, QCLASS) == receipt["path"]


# ------------------------------------------------------------ create and status through Flask

@pytest.fixture(autouse=True)
def _known_fields(monkeypatch):
    """The saved class has the fields of a square pulse (the env probe that
    would list them is not run here)."""
    from quam_state_manager.core import pulse_catalog
    real = pulse_catalog.by_qclass
    monkeypatch.setattr(pulse_catalog, "by_qclass",
                        lambda q: real("SquarePulse") if q == QCLASS else real(q))

@pytest.fixture
def made(env, monkeypatch):  # noqa: F811
    """The class saved to quam_config, then a gate made with it."""
    assert _save(env).status_code == 200
    fake = _fake()
    monkeypatch.setattr(czr, "run_check", fake)
    monkeypatch.setattr(czr, "_env_python", lambda: "C:/fake/python.exe")
    j = env.c.post(CHECK, json=_sm_class_body()).get_json()
    assert j["pairs"][0]["verdict"]["allowed"], j["pairs"][0]
    r = env.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    assert r.status_code == 200 and r.get_json()["count"] == 1, r.get_json()
    env.fake = fake
    return env


def _macro_of(a):
    return a.store.merged["qubit_pairs"][PAIR]["macros"][GATE]


def test_create_writes_the_receipt_into_extras_and_the_check_saw_it(made):
    target = made.folder / f"{CLASS}.py"
    files = GS.sm_class_files(_macro_of(made))
    assert files == [{"qclass": QCLASS, "file": f"{CLASS}.py",
                      "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                      "proportional": True}]
    checked = dict(made.fake.calls[-1]["creates"])[MPATH]
    assert checked["extras"]["sm_class_files"] == files
    text = target.read_text(encoding="utf-8")
    state = json.dumps(made.store.state)
    assert text.splitlines()[0] not in state and "def " not in state


def test_a_gate_without_sm_classes_gets_no_extras(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(czr, "run_check", _fake())
    monkeypatch.setattr(czr, "_env_python", lambda: "C:/fake/python.exe")
    from tests.test_cz_gate_routes import _body
    j = env.c.post(CHECK, json=_body()).get_json()
    env.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    assert "sm_class_files" not in (_macro_of(env).get("extras") or {})
    assert GS.sm_class_files(_macro_of(env)) == []


def _status_html(a):
    return a.c.get(f"/pulse/cz-gate/status?pair={PAIR}&gate={GATE}").get_data(as_text=True)


def test_the_notice_stays_on_every_status_line(made):
    html = _status_html(made)
    assert NOTICE in html and "gate-status-recheck" not in html
    pair = made.c.get(f"/pair/{PAIR}", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert NOTICE in pair
    pulse = made.c.get(f"/pulse/detail?path={MPATH}.flux_pulse_qubit",
                       headers={"HX-Request": "true"}).get_data(as_text=True)
    assert 'class="gate-status"' in pulse and NOTICE in pulse


def test_missing_and_changed_files_show_on_the_line(made):
    target = made.folder / f"{CLASS}.py"
    keep = target.read_bytes()
    target.write_bytes(keep + b"\n# edited\n")
    html = _status_html(made)
    assert "class file changed since the gate was checked" in html and "gate-status-recheck" in html
    target.unlink()
    html = _status_html(made)
    assert f"class file missing: quam_config/{CLASS}.py" in html and "gate-status-recheck" not in html


def _gate_check(store):
    checks = {c: {"status": "pass", "message": "ok"} for c in gate_check.CHECKS}
    return {"status": "ok", "checked": True, "ok": True, "failed": False, "all_checked": True,
            "why_not_checked": None, "error": None, "seq": store.mutation_seq,
            "store_id": chip_copy.store_id(store),
            "gates": [{"pair": PAIR, "gate": GATE, "checks": checks, "ran": True,
                       "unchecked": [], "partly_checked": []}]}


def test_recheck_writes_the_new_hash_in_one_undo_step(made, monkeypatch):
    from quam_state_manager.core import config_generator
    monkeypatch.setattr(config_generator, "get_selected_env", lambda inst: "C:/fake/python.exe")
    target = made.folder / f"{CLASS}.py"
    old = GS.sm_class_files(_macro_of(made))
    # the class file as a later save left it: new bytes and a new receipt
    new_bytes = target.read_bytes() + b"\n# a later edit\n"
    target.write_bytes(new_bytes)
    rec_file = next(Path(made.app.instance_path).rglob(W.PUBLISHED_FILE))
    receipt = json.loads(rec_file.read_text(encoding="utf-8"))
    receipt["sha"] = hashlib.sha256(new_bytes).hexdigest()
    rec_file.write_text(json.dumps(receipt), encoding="utf-8")
    calls = []

    def fake(store, py, gates, creates=(), **kw):
        calls.append(list(gates))
        return _gate_check(store)
    monkeypatch.setattr(gate_check, "check_gates", fake)
    n = len(made.store.change_log)
    r = made.c.post("/api/pulse/cz-gate/recheck", json={"pair": PAIR, "gate": GATE})
    assert r.status_code == 200, r.get_json()
    assert calls == [[(PAIR, GATE)]]
    files = GS.sm_class_files(_macro_of(made))
    assert files[0]["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest() != old[0]["sha256"]
    assert len(made.store.change_log) == n + 1
    assert "class file present" in _status_html(made)
    assert made.c.post("/undo").status_code == 200
    assert GS.sm_class_files(_macro_of(made)) == old


def test_recheck_that_fails_the_lab_check_keeps_the_record(made, monkeypatch):
    from quam_state_manager.core import config_generator
    monkeypatch.setattr(config_generator, "get_selected_env", lambda inst: "C:/fake/python.exe")
    old = GS.sm_class_files(_macro_of(made))

    def fake(store, py, gates, creates=(), **kw):
        res = _gate_check(store)
        res["gates"][0]["checks"]["load_config"] = {"status": "fail", "message": "load failed"}
        return res
    monkeypatch.setattr(gate_check, "check_gates", fake)
    r = made.c.post("/api/pulse/cz-gate/recheck", json={"pair": PAIR, "gate": GATE})
    assert r.status_code == 400 and "load failed" in r.get_json()["error"]
    assert GS.sm_class_files(_macro_of(made)) == old


def test_a_gate_class_without_extras_slot_is_refused_with_the_reason(env, monkeypatch):  # noqa: F811
    assert _save(env).status_code == 200
    fake = _fake()

    def run(store, python_path, gates, creates, previews):
        out = fake(store, python_path, gates, creates, previews)
        out["gates"][0]["checks"]["load_config"] = {
            "status": "fail", "message": "The chip does not load: unexpected attribute 'extras'."}
        return out
    monkeypatch.setattr(czr, "run_check", run)
    monkeypatch.setattr(czr, "_env_python", lambda: "C:/fake/python.exe")
    before = env.snap()
    j = env.c.post(CHECK, json=_sm_class_body()).get_json()
    v = j["pairs"][0]["verdict"]
    assert not v["allowed"] and czr.EXTRAS_SLOT_REFUSAL in v["reason"]
    r = env.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    assert r.status_code == 400 and env.snap() == before


def test_a_gate_with_sm_classes_is_refused_when_not_checked(env, monkeypatch):  # noqa: F811
    assert _save(env).status_code == 200
    monkeypatch.setattr(czr, "run_check", _fake(status="no-env", checked=False))
    monkeypatch.setattr(czr, "_env_python", lambda: "C:/fake/python.exe")
    j = env.c.post(CHECK, json=_sm_class_body()).get_json()
    v = j["pairs"][0]["verdict"]
    assert not v["allowed"] and "uses a class SM made" in v["reason"]
