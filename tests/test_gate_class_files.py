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


def test_the_missing_status_is_a_warning_with_its_exact_why(tmp_path):
    a, b = tmp_path / f"{CLASS}.py", tmp_path / "Other.py"
    ea, eb = _entry(a), _entry(b)
    eb["qclass"] = "quam_config.Other.Other"
    a.unlink()
    b.unlink()
    one, _ = _items(_macro([ea]), lambda q: str(a))
    assert one["class"]["tone"] == "warn"
    assert one["class"]["why"] == (f"The gate uses {QCLASS}, and its file is not in the folder "
                                   "it was saved to.")
    two, _ = _items(_macro([ea, eb]), lambda q: str(a) if q == QCLASS else str(b))
    assert two["class"]["text"] == f"class file missing: quam_config/{CLASS}.py, quam_config/Other.py"
    assert two["class"]["tone"] == "warn"
    assert two["class"]["why"] == (f"The gate uses {QCLASS}, quam_config.Other.Other, and their "
                                   "files are not in the folder they were saved to.")


def test_a_record_whose_file_is_not_a_string_is_dropped():
    good = {"qclass": QCLASS, "file": "A.py", "sha256": "ab", "proportional": None}
    for bad in ({"qclass": QCLASS, "file": 7}, {"qclass": QCLASS, "file": None}, {"qclass": QCLASS}):
        assert GS.sm_class_files(_macro([bad, good])) == [good]


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


def _published(tmp_path, data=b"class A: pass\n", sha="abc"):
    inst, scope = tmp_path / "inst", "chip"
    folder = D.draft_path(inst, scope, CLASS).parent
    folder.mkdir(parents=True)
    target = tmp_path / f"{CLASS}.py"
    if data is not None:
        target.write_bytes(data)
    receipt = {"path": str(target), "sha": sha, "importable": True,
               "qclass": QCLASS, "proportional": {"proportional": False, "ratio_min": 1}}
    (folder / W.PUBLISHED_FILE).write_text(json.dumps(receipt), encoding="utf-8")
    return inst, scope, target


def test_class_files_hash_the_published_bytes_not_the_receipt(tmp_path):
    inst, scope, target = _published(tmp_path)
    sha = hashlib.sha256(target.read_bytes()).hexdigest()
    assert Q.class_files(inst, scope, classes=[QCLASS]) == [
        {"qclass": QCLASS, "file": f"{CLASS}.py", "sha256": sha, "proportional": False}]
    assert Q.class_files(inst, scope, classes=["quam.Other"]) == []
    assert Q.published_path(inst, scope, QCLASS) == str(target)


def test_a_missing_published_file_refuses_naming_the_file(tmp_path):
    inst, scope, _ = _published(tmp_path, data=None)
    with pytest.raises(Q.ClassFileError, match=rf"quam_config/{CLASS}\.py"):
        Q.class_files(inst, scope, classes=[QCLASS])


def test_class_files_moved_names_a_file_whose_bytes_changed(tmp_path):
    inst, scope, target = _published(tmp_path)
    files = Q.class_files(inst, scope, classes=[QCLASS])
    assert Q.class_files_moved(inst, scope, files) is None
    target.write_bytes(target.read_bytes() + b"#")
    assert f"quam_config/{CLASS}.py" in Q.class_files_moved(inst, scope, files)
    target.unlink()
    assert f"quam_config/{CLASS}.py" in Q.class_files_moved(inst, scope, files)


def test_scaling_any_not_proportional_wins_over_checked(tmp_path):
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    files = [_entry(a, proportional=True), _entry(b, proportional=False)]
    for order in (files, files[::-1]):
        items, _ = _items(_macro(order), lambda q: None)
        assert items["scaling"]["text"] == "amplitude scaling: not proportional"


def test_a_missing_or_changed_file_outranks_class_not_importable(tmp_path):
    f = tmp_path / f"{CLASS}.py"
    e = _entry(f)
    cls = "quam_config.Slot.Slot"

    def text():
        out = GS.status(_Store(_macro([e], cls=cls)), None, "p", "g", class_path=lambda q: str(f),
                        missing_classes=[cls], env_name="env")
        return next(i["text"] for i in out if i["key"] == "class")
    assert text() == "class Slot not importable in env"
    f.write_bytes(b"other\n")
    assert text() == "class file changed since the gate was checked"
    f.unlink()
    assert text() == f"class file missing: quam_config/{CLASS}.py"


def test_may_create_with_sm_classes_needs_load_and_ops_to_pass():
    from quam_state_manager.core import cz_gate_create as C
    checks = {c: {"status": "pass", "message": "ok"} for c in gate_check.CHECKS}
    checks["ops"] = {"status": "skipped", "message": "ops could not run"}
    entry = {"pair": PAIR, "gate": GATE, "checks": checks, "ran": True, "unchecked": []}
    result = {"status": "ok", "checked": True, "gates": [entry]}
    known = sorted(C.GATE_CLASSES)[0]
    assert C.may_create(result, entry, gate_class=known, layout="known")[0] is True
    assert C.may_create(result, entry, gate_class=known, layout="known", sm_classes=True)[0] is False
    checks["ops"] = {"status": "pass", "message": "ok"}
    checks["load_config"] = {"status": "skipped", "message": "no load"}
    assert C.may_create(result, entry, gate_class=known, layout="known", sm_classes=True)[0] is False


def test_copied_layout_settings_leave_out_extras():
    plan = {"layout": "copied", "creates": [(MPATH, {"__class__": "x.Y", "extras": {"a": 1},
                                                     "phase": 0.5})],
            "fields": {}, "operations": {}, "reset": []}
    assert [s["key"] for s in czr._settings(plan)] == ["phase"]


def test_recorded_class_files_merge_into_existing_extras():
    rec = [{"qclass": QCLASS, "file": f"{CLASS}.py", "sha256": "ab", "proportional": None}]
    plan = {"creates": [(MPATH, {"__class__": "x.Y", "extras": {"note": "keep"}})]}
    czr._record_class_files(plan, PAIR, GATE, rec)
    assert plan["creates"][0][1]["extras"] == {"note": "keep", "sm_class_files": rec}
    assert plan["sm_class_files"] == rec


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


def test_recheck_is_offered_only_on_a_changed_file(made):
    target = made.folder / f"{CLASS}.py"
    keep = target.read_bytes()
    assert "gate-status-recheck" not in _status_html(made)          # present
    target.write_bytes(keep + b"\n# edited\n")
    assert "gate-status-recheck" in _status_html(made)              # changed
    target.unlink()
    assert "gate-status-recheck" not in _status_html(made)          # missing
    target.write_bytes(keep)
    _macro_of(made).get("extras", {}).pop("sm_class_files")
    html = _status_html(made)                                       # not an SM-made class
    assert "not a class SM made" in html and "gate-status-recheck" not in html


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


def test_the_needs_notice_is_a_detail_not_a_pill(made):
    from quam_state_manager.web import gate_status_routes as gsr
    with made.app.test_request_context("/"):
        line = gsr.gate_line(made.store, None, PAIR, GATE, env=([], None), folder=None)
    needs = [i for i in line["items"] if i["key"] == "needs"]
    assert [n["text"] for n in needs] == [NOTICE] and needs[0]["pill"] is False
    assert all(i["key"] != "needs" for i in line["pills"])


# ------------------------------------------------------------ hashes are the file's bytes

def _check_create(a, monkeypatch, run=None):
    monkeypatch.setattr(czr, "run_check", run or _fake())
    monkeypatch.setattr(czr, "_env_python", lambda: "C:/fake/python.exe")
    j = a.c.post(CHECK, json=_sm_class_body()).get_json()
    r = a.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    return j, r


def test_a_file_edited_outside_sm_then_created_reads_present(env, monkeypatch):  # noqa: F811
    assert _save(env).status_code == 200
    target = env.folder / f"{CLASS}.py"
    target.write_bytes(target.read_bytes() + b"\n# edited outside SM\n")
    _, r = _check_create(env, monkeypatch)
    assert r.status_code == 200, r.get_json()
    assert GS.sm_class_files(_macro_of(env))[0]["sha256"] == hashlib.sha256(
        target.read_bytes()).hexdigest()
    html = _status_html(env)
    assert "class file present" in html and "class file changed" not in html
    target.write_bytes(target.read_bytes() + b"# later\n")
    assert "class file changed since the gate was checked" in _status_html(env)


def test_bytes_changed_during_the_check_refuse_the_create(env, monkeypatch):  # noqa: F811
    assert _save(env).status_code == 200
    target = env.folder / f"{CLASS}.py"
    fake = _fake()

    def run(store, python_path, gates, creates, previews):
        target.write_bytes(target.read_bytes() + b"# during the check\n")
        return fake(store, python_path, gates, creates, previews)
    before = env.snap()
    j, r = _check_create(env, monkeypatch, run)
    v = j["pairs"][0]["verdict"]
    assert not v["allowed"] and f"quam_config/{CLASS}.py" in v["reason"]
    assert "changed while the gate was checked" in v["reason"]
    assert r.status_code == 400 and env.snap() == before


def test_a_published_file_that_is_gone_refuses_the_check(env, monkeypatch):  # noqa: F811
    assert _save(env).status_code == 200
    target = env.folder / f"{CLASS}.py"
    real = czr.class_refusal
    seen = []

    def refusal(*a, **k):
        # the file goes after the last refusal check, before the record is made
        out = real(*a, **k)
        seen.append(1)
        if len(seen) == 2 and target.exists():
            target.unlink()
        return out
    monkeypatch.setattr(czr, "class_refusal", refusal)
    before = env.snap()
    j, r = _check_create(env, monkeypatch)
    refusals = " ".join(j["pairs"][0].get("refusals") or [])
    assert f"quam_config/{CLASS}.py is not in the folder it was saved to" in refusals
    assert r.status_code == 400 and env.snap() == before


def _recheck_env(monkeypatch):
    from quam_state_manager.core import config_generator
    monkeypatch.setattr(config_generator, "get_selected_env", lambda inst: "C:/fake/python.exe")


def test_recheck_after_an_outside_edit_reads_present(made, monkeypatch):
    _recheck_env(monkeypatch)
    target = made.folder / f"{CLASS}.py"
    target.write_bytes(target.read_bytes() + b"\n# edited outside SM\n")
    assert "class file changed since the gate was checked" in _status_html(made)
    monkeypatch.setattr(gate_check, "check_gates",
                        lambda store, py, gates, creates=(), **kw: _gate_check(store))
    r = made.c.post("/api/pulse/cz-gate/recheck", json={"pair": PAIR, "gate": GATE})
    assert r.status_code == 200, r.get_json()
    html = _status_html(made)
    assert "class file present" in html and "class file changed" not in html


def test_recheck_refuses_bytes_changed_during_the_check(made, monkeypatch):
    _recheck_env(monkeypatch)
    target = made.folder / f"{CLASS}.py"
    target.write_bytes(target.read_bytes() + b"\n# edited\n")
    old = GS.sm_class_files(_macro_of(made))
    n = len(made.store.change_log)

    def fake(store, py, gates, creates=(), **kw):
        target.write_bytes(target.read_bytes() + b"# during the check\n")
        return _gate_check(store)
    monkeypatch.setattr(gate_check, "check_gates", fake)
    r = made.c.post("/api/pulse/cz-gate/recheck", json={"pair": PAIR, "gate": GATE})
    assert r.status_code == 409 and f"quam_config/{CLASS}.py" in r.get_json()["error"]
    assert GS.sm_class_files(_macro_of(made)) == old and len(made.store.change_log) == n


def test_recheck_refuses_when_the_class_is_not_published_any_more(made, monkeypatch):
    _recheck_env(monkeypatch)
    old = GS.sm_class_files(_macro_of(made))
    next(Path(made.app.instance_path).rglob(W.PUBLISHED_FILE)).write_text("{}", encoding="utf-8")
    calls = []

    def fake(store, py, gates, creates=(), **kw):
        calls.append(gates)
        return _gate_check(store)
    monkeypatch.setattr(gate_check, "check_gates", fake)
    r = made.c.post("/api/pulse/cz-gate/recheck", json={"pair": PAIR, "gate": GATE})
    assert r.status_code == 409 and "not saved to quam_config any more" in r.get_json()["error"]
    assert calls == [] and GS.sm_class_files(_macro_of(made)) == old


def test_recheck_refuses_a_published_file_that_is_gone(made, monkeypatch):
    _recheck_env(monkeypatch)
    old = GS.sm_class_files(_macro_of(made))
    (made.folder / f"{CLASS}.py").unlink()
    r = made.c.post("/api/pulse/cz-gate/recheck", json={"pair": PAIR, "gate": GATE})
    assert r.status_code == 409 and f"quam_config/{CLASS}.py" in r.get_json()["error"]
    assert GS.sm_class_files(_macro_of(made)) == old


def test_recheck_refuses_when_the_chip_changed_during_the_check(made, monkeypatch):
    _recheck_env(monkeypatch)
    target = made.folder / f"{CLASS}.py"
    target.write_bytes(target.read_bytes() + b"\n# edited\n")
    old = GS.sm_class_files(_macro_of(made))

    def fake(store, py, gates, creates=(), **kw):
        out = _gate_check(store)
        with store._lock:
            store.mutation_seq += 1          # another edit landed while the check ran
        return out
    monkeypatch.setattr(gate_check, "check_gates", fake)
    r = made.c.post("/api/pulse/cz-gate/recheck", json={"pair": PAIR, "gate": GATE})
    assert r.status_code == 409 and r.get_json().get("stale") is True
    assert GS.sm_class_files(_macro_of(made)) == old


# ------------------------------------------------------------ a gate with two SM-made classes

CLASS2 = "RampPulse"
QCLASS2 = f"quam_config.{CLASS2}.{CLASS2}"


def _two_class_body():
    b = _sm_class_body()
    b["pulses"]["coupler_flux_pulse"] = {"class": QCLASS2, "fields": {"length": 48, "amplitude": 0.1}}
    return b


@pytest.fixture
def made_two(env, monkeypatch):  # noqa: F811
    """Two classes saved to quam_config, then a gate made with both."""
    from quam_state_manager.core import pulse_catalog
    real = pulse_catalog.by_qclass
    monkeypatch.setattr(pulse_catalog, "by_qclass",
                        lambda q: real("SquarePulse") if q in (QCLASS, QCLASS2) else real(q))
    assert _save(env).status_code == 200
    assert env.c.post("/pulse-class/new", json={"cls": CLASS2}).status_code == 200
    first = env.c.post("/pulse-class/save-config", json={"cls": CLASS2, "python": "py"}).get_json()
    r = env.c.post("/pulse-class/save-config", json={"cls": CLASS2, "python": "py",
                                                     "confirmed_folder": first["confirm"]["folder"]})
    assert r.status_code == 200, r.get_json()
    fake = _fake()
    monkeypatch.setattr(czr, "run_check", fake)
    monkeypatch.setattr(czr, "_env_python", lambda: "C:/fake/python.exe")
    j = env.c.post(CHECK, json=_two_class_body()).get_json()
    assert j["pairs"][0]["verdict"]["allowed"], j["pairs"][0]
    r = env.c.post(CREATE, json={"check_token": j["check_token"], "plan_sig": j["plan_sig"]})
    assert r.status_code == 200 and r.get_json()["count"] == 1, r.get_json()
    env.fake = fake
    return env


def test_two_classes_are_recorded_sorted_and_the_check_saw_both(made_two):
    files = GS.sm_class_files(_macro_of(made_two))
    assert [f["qclass"] for f in files] == sorted([QCLASS, QCLASS2])
    assert [f["file"] for f in files] == [f"{CLASS}.py", f"{CLASS2}.py"]
    for f in files:
        assert f["sha256"] == hashlib.sha256((made_two.folder / f["file"]).read_bytes()).hexdigest()
    checked = dict(made_two.fake.calls[-1]["creates"])[MPATH]
    assert checked["extras"]["sm_class_files"] == files


def test_every_class_of_a_gate_gets_its_own_notice(made_two):
    html = _status_html(made_two)
    for name in (CLASS, CLASS2):
        line = (f'<div class="ui-text-quiet" data-status-key="needs">this chip now needs '
                f"quam_config/{name}.py; every computer that opens this state needs the same "
                "file</div>")
        assert html.count(line) == 1, name


def test_a_missing_file_is_shown_before_a_changed_one(made_two):
    a, b = made_two.folder / f"{CLASS}.py", made_two.folder / f"{CLASS2}.py"
    a.write_bytes(a.read_bytes() + b"\n# edited\n")
    b.unlink()
    html = _status_html(made_two)
    assert f"class file missing: quam_config/{CLASS2}.py" in html
    assert "class file changed since the gate was checked" not in html
    assert "gate-status-recheck" not in html
    # and the other way round: the first missing, the second changed
    a.unlink()
    b.write_bytes(b"class B: pass\n")
    html = _status_html(made_two)
    assert f"class file missing: quam_config/{CLASS}.py" in html
    assert "gate-status-recheck" not in html


def test_recheck_refreshes_the_hash_of_every_class_file(made_two, monkeypatch):
    from quam_state_manager.core import config_generator
    monkeypatch.setattr(config_generator, "get_selected_env", lambda inst: "C:/fake/python.exe")
    old = GS.sm_class_files(_macro_of(made_two))
    for name in (CLASS, CLASS2):
        target = made_two.folder / f"{name}.py"
        data = target.read_bytes() + b"\n# a later edit\n"
        target.write_bytes(data)
        rec = next(p for p in Path(made_two.app.instance_path).rglob(W.PUBLISHED_FILE)
                   if p.parent.name == name)
        receipt = json.loads(rec.read_text(encoding="utf-8"))
        receipt["sha"] = hashlib.sha256(data).hexdigest()
        rec.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(gate_check, "check_gates",
                        lambda store, py, gates, creates=(), **kw: _gate_check(store))
    assert "gate-status-recheck" in _status_html(made_two)
    r = made_two.c.post("/api/pulse/cz-gate/recheck", json={"pair": PAIR, "gate": GATE})
    assert r.status_code == 200, r.get_json()
    new = GS.sm_class_files(_macro_of(made_two))
    assert len(new) == 2
    for o, n in zip(old, new):
        assert n["sha256"] == hashlib.sha256((made_two.folder / n["file"]).read_bytes()).hexdigest()
        assert n["sha256"] != o["sha256"]
    assert "gate-status-recheck" not in _status_html(made_two)
    assert made_two.c.post("/undo").status_code == 200
    assert GS.sm_class_files(_macro_of(made_two)) == old


def test_every_missing_file_is_named_and_every_changed_file_listed(made_two):
    a, b = made_two.folder / f"{CLASS}.py", made_two.folder / f"{CLASS2}.py"
    a.write_bytes(a.read_bytes() + b"\n# edited\n")
    b.write_bytes(b.read_bytes() + b"\n# edited\n")
    html = _status_html(made_two)
    assert f"{CLASS}.py, {CLASS2}.py. Re-check" in html
    a.unlink()
    b.unlink()
    html = _status_html(made_two)
    assert f"class file missing: quam_config/{CLASS}.py, quam_config/{CLASS2}.py" in html


def test_recheck_refuses_when_one_of_two_classes_is_not_published_any_more(made_two, monkeypatch):
    from quam_state_manager.core import config_generator
    monkeypatch.setattr(config_generator, "get_selected_env", lambda inst: "C:/fake/python.exe")
    old = GS.sm_class_files(_macro_of(made_two))
    n = len(made_two.store.change_log)
    rec = next(p for p in Path(made_two.app.instance_path).rglob(W.PUBLISHED_FILE)
               if p.parent.name == CLASS2)
    rec.write_text("{}", encoding="utf-8")
    calls = []
    monkeypatch.setattr(gate_check, "check_gates",
                        lambda store, py, gates, creates=(), **kw: calls.append(gates)
                        or _gate_check(store))
    r = made_two.c.post("/api/pulse/cz-gate/recheck", json={"pair": PAIR, "gate": GATE})
    assert r.status_code == 409 and "not saved to quam_config any more" in r.get_json()["error"]
    assert calls == [] and GS.sm_class_files(_macro_of(made_two)) == old
    assert len(made_two.store.change_log) == n
