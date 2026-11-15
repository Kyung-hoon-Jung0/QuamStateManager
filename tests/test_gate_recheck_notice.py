"""The chip-level notice while gates wait for Re-check: which gates it lists
(core/gate_status.gates_to_recheck), where it shows (every pair's gate list,
the Pulses page) and how it goes away (Re-check, delete, undo, pull)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from quam_state_manager.core import gate_check
from quam_state_manager.core import gate_status as GS
from quam_state_manager.core import pulse_class_watch as W
from quam_state_manager.web import gate_status_routes as routes
from tests.cz_gate_chip import add_gate
from tests.test_gate_class_files import CLASS, GATE, PAIR, _gate_check, _known_fields, made  # noqa: F401
from tests.test_gate_status_routes import _ctx, _merged, rig  # noqa: F401
from tests.test_quam_config_save_routes import env  # noqa: F401

SENTENCE = "Other chips that use this file were not checked."
QA, QB = "quam_config.ShapeA.ShapeA", "quam_config.ShapeB.ShapeB"


def _record(path: Path, qclass: str, data: bytes = b"class A: pass\n") -> dict:
    path.write_bytes(data)
    return {"qclass": qclass, "file": path.name,
            "sha256": hashlib.sha256(data).hexdigest(), "proportional": True}


def _chip(*gates):
    """``(pair, gate, records)`` -> a merged dict."""
    pairs: dict = {}
    for pair, gate, records in gates:
        macro = {"__class__": "quam_builder.CZGate"}
        if records is not None:
            macro["extras"] = {"sm_class_files": records}
        pairs.setdefault(pair, {"macros": {}})["macros"][gate] = macro
    return {"qubit_pairs": pairs}


@pytest.fixture
def two(tmp_path):
    a, b = tmp_path / "ShapeA.py", tmp_path / "ShapeB.py"
    ra, rb = _record(a, QA), _record(b, QB)
    where = {QA: str(a), QB: str(b)}.get
    return a, b, ra, rb, where


# ------------------------------------------------------------ core

def test_two_changed_gates_on_two_pairs_are_both_listed(two):
    a, b, ra, rb, where = two
    merged = _chip(("p1", "cz_a", [ra]), ("p2", "cz_b", [rb]))
    assert GS.gates_to_recheck(merged, where) == []
    a.write_bytes(b"class A: changed = 1\n")
    b.write_bytes(b"class B: changed = 1\n")
    assert GS.gates_to_recheck(merged, where) == [
        {"pair": "p1", "gate": "cz_a", "files": ["ShapeA.py"]},
        {"pair": "p2", "gate": "cz_b", "files": ["ShapeB.py"]}]
    # Re-check of the first gate records the new bytes: only the other waits
    ra["sha256"] = hashlib.sha256(a.read_bytes()).hexdigest()
    assert GS.gates_to_recheck(merged, where) == [
        {"pair": "p2", "gate": "cz_b", "files": ["ShapeB.py"]}]
    rb["sha256"] = hashlib.sha256(b.read_bytes()).hexdigest().upper()
    assert GS.gates_to_recheck(merged, where) == []


def test_a_missing_file_is_not_listed(two):
    a, b, ra, rb, where = two
    merged = _chip(("p1", "cz_a", [ra]), ("p2", "cz_b", [rb, _record(b.with_name("C.py"), "q.C")]))
    a.unlink()
    assert GS.gates_to_recheck(merged, where) == []
    # a gate with one changed and one missing file says "missing" first; not listed
    b.write_bytes(b"changed")
    assert GS.gates_to_recheck(merged, where) == []


def test_a_gate_without_sm_records_is_not_listed(two):
    _a, _b, _ra, _rb, where = two
    merged = _chip(("p1", "cz_lab", None), ("p1", "cz_empty", []))
    assert GS.gates_to_recheck(merged, where) == []


@pytest.mark.parametrize("bad", [
    {"file": 7, "qclass": QA, "sha256": "ab"},
    {"file": "ShapeA.py", "qclass": QA},
    {"file": "ShapeA.py", "qclass": ["not", "hashable"], "sha256": "ab"},
    "not a record", None])
def test_a_bad_record_does_not_crash_and_is_not_listed(two, bad):
    a, b, ra, rb, where = two
    b.write_bytes(b"changed")
    merged = _chip(("p1", "cz_a", [bad]), ("p2", "cz_b", [rb]))
    merged["qubit_pairs"]["p0"] = "not a pair"
    merged["qubit_pairs"]["p3"] = {"macros": ["not", "a", "dict"]}
    merged["qubit_pairs"]["p1"]["macros"]["cz"] = "#./cz_a"
    assert GS.gates_to_recheck(merged, where) == [
        {"pair": "p2", "gate": "cz_b", "files": ["ShapeB.py"]}]
    assert GS.gates_to_recheck("not a chip", where) == []

    def broken(qclass):
        raise TypeError("no path")
    assert GS.gates_to_recheck(merged, broken) == []


# ------------------------------------------------------------ routes

@pytest.fixture
def chip2(rig, tmp_path, monkeypatch):
    """rig with cz_sq (q1-q2) and a new cz_b (q2-q3) recording SM class files."""
    m = _ctx(rig)["modifier"]
    _s, plan = add_gate(_merged(rig), pair="q2-q3", name="cz_b")
    for path, value in plan["creates"]:
        m.create_subtree(path, value)
    a, b = tmp_path / "ShapeA.py", tmp_path / "ShapeB.py"
    ra, rb = _record(a, QA), _record(b, QB)
    m.set_value("qubit_pairs.q1-q2.macros.cz_sq.extras", {"sm_class_files": [ra]})
    m.set_value("qubit_pairs.q2-q3.macros.cz_b.extras", {"sm_class_files": [rb]})
    paths = {QA: str(a), QB: str(b)}
    monkeypatch.setattr(routes.quam_config_save, "published_path",
                        lambda instance, scope, qclass: paths.get(qclass))
    rig._files = (a, b)
    return rig


def _page(c, url):
    return c.get(url, headers={"HX-Request": "true"}).get_data(as_text=True)


def _views(c):
    return {"q1-q2": _page(c, "/pair/q1-q2"), "q2-q3": _page(c, "/pair/q2-q3"),
            "q2-q1": _page(c, "/pair/q2-q1"), "pulses": _page(c, "/pulses"),
            "slot": _page(c, "/pulse/cz-gate/recheck-notice")}


def _rechecked(c, gate_path, path):
    _ctx(c)["modifier"].set_value(gate_path + ".extras.sm_class_files.0.sha256",
                                  hashlib.sha256(path.read_bytes()).hexdigest())


def test_the_notice_lists_both_gates_everywhere_then_goes(chip2):
    a, b = chip2._files
    for name, html in _views(chip2).items():
        assert "data-recheck-notice" not in html and SENTENCE not in html, name
    a.write_bytes(b"changed a")
    b.write_bytes(b"changed b")
    for name, html in _views(chip2).items():
        assert html.count('data-recheck-notice="') == 1, name
        assert "2 gates wait for Re-check" in html, name
        assert "q1-q2 / cz_sq (ShapeA.py)" in html and "q2-q3 / cz_b (ShapeB.py)" in html, name
        assert SENTENCE in html and "ui-callout ui-callout--warn" in html, name
    _rechecked(chip2, "qubit_pairs.q1-q2.macros.cz_sq", a)
    for name, html in _views(chip2).items():
        assert "1 gate waits for Re-check" in html, name
        assert "q2-q3 / cz_b (ShapeB.py)" in html and "cz_sq (ShapeA.py)" not in html, name
        assert SENTENCE in html, name
    _rechecked(chip2, "qubit_pairs.q2-q3.macros.cz_b", b)
    views = _views(chip2)
    for name, html in views.items():
        assert "data-recheck-notice" not in html and SENTENCE not in html, name
        assert "wait for Re-check" not in html and "waits for Re-check" not in html, name
    assert views["slot"].strip() == ""
    # undo brings the second gate's old record back, and the notice with it
    assert chip2.post("/undo").status_code == 200
    assert "1 gate waits for Re-check" in _page(chip2, "/pair/q1-q2")


def test_a_deleted_gate_leaves_the_notice(chip2):
    a, _b = chip2._files
    a.write_bytes(b"changed a")
    assert "q1-q2 / cz_sq (ShapeA.py)" in _page(chip2, "/pair/q2-q3")
    _ctx(chip2)["modifier"].delete_subtree("qubit_pairs.q1-q2.macros.cz_sq")
    assert "data-recheck-notice" not in _page(chip2, "/pair/q2-q3")
    assert _page(chip2, "/pulse/cz-gate/recheck-notice").strip() == ""


def test_the_notice_never_blocks_a_press(chip2):
    """Make active of a gate with no class file problem goes through while
    another gate waits for Re-check."""
    _a, b = chip2._files
    b.write_bytes(b"changed b")
    assert "1 gate waits for Re-check" in _page(chip2, "/pair/q1-q2")
    m = _ctx(chip2)["modifier"]
    m.delete_subtree("qubit_pairs.q1-q2.macros.cz_sq.extras")
    r = chip2.post("/api/pulse/cz-gate/make-active",
                   data={"pair": "q1-q2", "gate": "cz_sq", "scope": "list"},
                   headers={"HX-Request": "true"})
    body = r.get_data(as_text=True)
    assert r.status_code == 200, body[:300]
    assert _merged(chip2)["qubit_pairs"]["q1-q2"]["macros"]["cz"] == "#./cz_sq"
    # the list's own answer carries the notice of the gate that still waits
    assert "gate-status-list" in body and "1 gate waits for Re-check" in body
    assert "q2-q3 / cz_b (ShapeB.py)" in body and SENTENCE in body


def test_the_pulses_slot_refreshes_on_the_gate_list_events(chip2):
    html = _page(chip2, "/pulses")
    start = html.index('id="gate-recheck-slot"')
    tag = html[html.rindex("<div", 0, start):html.index(">", start)]
    assert 'hx-get="/pulse/cz-gate/recheck-notice"' in tag
    trigger = tag.split('hx-trigger="', 1)[1].split('"', 1)[0]
    assert set(t.strip() for t in trigger.split(",")) == {
        "pulses-changed from:body", "cellsReverted from:document", "stateRestored from:document"}
    # the pair's gate list refreshes on the same undo / pull events
    zone = _page(chip2, "/pair/q1-q2")
    assert "cellsReverted from:document, stateRestored from:document" in zone


def test_recheck_in_the_list_answers_without_the_notice(made, monkeypatch):  # noqa: F811
    from quam_state_manager.core import config_generator
    monkeypatch.setattr(config_generator, "get_selected_env", lambda inst: "C:/fake/python.exe")
    target = made.folder / f"{CLASS}.py"
    new_bytes = target.read_bytes() + b"\n# a later edit\n"
    target.write_bytes(new_bytes)
    rec_file = next(Path(made.app.instance_path).rglob(W.PUBLISHED_FILE))
    receipt = json.loads(rec_file.read_text(encoding="utf-8"))
    receipt["sha"] = hashlib.sha256(new_bytes).hexdigest()
    rec_file.write_text(json.dumps(receipt), encoding="utf-8")
    monkeypatch.setattr(gate_check, "check_gates",
                        lambda store, py, gates, creates=(), **kw: _gate_check(store))
    before = made.c.get(f"/pair/{PAIR}", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert f"{PAIR} / {GATE} ({CLASS}.py)" in before and SENTENCE in before
    r = made.c.post("/api/pulse/cz-gate/recheck", data={"pair": PAIR, "gate": GATE, "scope": "list"},
                    headers={"HX-Request": "true"})
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "gate-status-list" in body, body[:300]
    assert "data-recheck-notice" not in body and SENTENCE not in body
    assert "pulses-changed" in r.headers.get("HX-Trigger", "")
    assert made.c.get("/pulse/cz-gate/recheck-notice").get_data(as_text=True).strip() == ""
