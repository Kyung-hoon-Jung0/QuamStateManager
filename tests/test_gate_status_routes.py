"""The gate status routes: the status partial, Make active, the delete offer,
and the status line in the pair detail and the pulse detail."""
from __future__ import annotations

import json

import pytest

from quam_state_manager.core import chip_copy, config_generator, gate_check
from quam_state_manager.core import gate_check_log as log
from quam_state_manager.web.app import create_app
from tests.cz_gate_chip import add_gate, chip, write

ENV = "C:/env/python.exe"
ALIAS = "qubit_pairs.q1-q2.macros.cz"


def _check(store, *, all_checked=True, fail=None, ran=True, status="ok"):
    checks = {c: {"status": "pass", "message": "ok"} for c in gate_check.CHECKS}
    if fail:
        checks["ops"] = {"status": "fail", "message": fail}
    if not all_checked:
        checks = {c: {"status": "skipped", "message": "not run"} for c in gate_check.CHECKS}
    return {"status": status, "checked": all_checked or bool(fail), "ok": all_checked and not fail,
            "failed": bool(fail), "all_checked": all_checked and not fail,
            "why_not_checked": None if all_checked else "no Python environment is selected",
            "error": None, "seq": store.mutation_seq if status == "ok" else None,
            "store_id": chip_copy.store_id(store) if status == "ok" else None,
            "gates": [{"pair": "q1-q2", "gate": "cz_sq", "checks": checks,
                       "ran": ran and not fail and all_checked, "unchecked": [],
                       "partly_checked": []}]}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    s, _ = add_gate(chip())
    s, _ = add_gate(s, name="cz_two")
    s["qubit_pairs"]["q1-q2"]["macros"]["cz"] = "#./cz_two"
    folder = write(tmp_path / "chip", s)
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    monkeypatch.setattr(config_generator, "get_selected_env", lambda inst: ENV)
    calls = []

    def fake(store, py, gates, creates=(), **kw):
        calls.append((py, list(gates), list(creates)))
        return ANSWER["fn"](store)
    ANSWER["fn"] = lambda store: _check(store)
    monkeypatch.setattr(gate_check, "check_gates", fake)
    c._app = app
    c._calls = calls
    yield c


ANSWER: dict = {}


def _ctx(c):
    return next(iter(c._app.config["contexts"].values()))


def _merged(c):
    return _ctx(c)["store"].merged


def _ma(c, gate="cz_sq", **kw):
    return c.post("/api/pulse/cz-gate/make-active", json={"pair": "q1-q2", "gate": gate}, **kw)


def test_make_active_sets_the_alias_in_one_undo(rig):
    before = json.dumps(_ctx(rig)["store"].state, sort_keys=False)
    r = _ma(rig)
    assert r.status_code == 200, r.data[:400]
    assert r.get_json()["ok"]
    assert _merged(rig)["qubit_pairs"]["q1-q2"]["macros"]["cz"] == "#./cz_sq"
    assert rig._calls and rig._calls[0][2] == [] and rig._calls[0][1] == [("q1-q2", "cz_sq")]
    assert rig.post("/undo").status_code == 200
    assert json.dumps(_ctx(rig)["store"].state, sort_keys=False) == before


def test_make_active_records_the_check(rig):
    assert _ma(rig).status_code == 200
    hm = rig._app.config["history_manager"]
    ent = log.entry(log.chip_dir(hm, _ctx(rig)["path"]), "q1-q2", "cz_sq")
    assert ent and ent["result"] == log.CHECKED
    assert ent["hash"] == log.content_hash(_merged(rig), "q1-q2", "cz_sq")


def test_make_active_creates_an_absent_alias(rig):
    m = _ctx(rig)["modifier"]
    m.delete_subtree(ALIAS)
    assert _ma(rig).status_code == 200
    assert _merged(rig)["qubit_pairs"]["q1-q2"]["macros"]["cz"] == "#./cz_sq"


def test_a_broken_gate_is_refused_and_the_alias_stays(rig):
    msg = "The gate plays cz_sq_pulse_q1_q2, which is not an operation of channel q1.z."
    ANSWER["fn"] = lambda store: _check(store, fail=msg)
    r = _ma(rig)
    assert r.status_code == 400
    assert "cz_sq_pulse_q1_q2" in r.get_json()["error"] and "q1.z" in r.get_json()["error"]
    assert _merged(rig)["qubit_pairs"]["q1-q2"]["macros"]["cz"] == "#./cz_two"


def test_a_check_that_could_not_run_allows_a_known_class_and_records_it(rig):
    ANSWER["fn"] = lambda store: _check(store, all_checked=False, status="no-env")
    r = _ma(rig)
    assert r.status_code == 200, r.data[:300]
    hm = rig._app.config["history_manager"]
    ent = log.entry(log.chip_dir(hm, _ctx(rig)["path"]), "q1-q2", "cz_sq")
    assert ent["result"] == log.NOT_CHECKED and "environment" in ent["reason"]


def test_a_check_that_could_not_run_refuses_an_unknown_class(rig):
    _ctx(rig)["modifier"].set_value("qubit_pairs.q1-q2.macros.cz_sq.__class__", "lab.g.MyCZ")
    ANSWER["fn"] = lambda store: _check(store, all_checked=False, status="no-env")
    r = _ma(rig)
    assert r.status_code == 400
    assert _merged(rig)["qubit_pairs"]["q1-q2"]["macros"]["cz"] == "#./cz_two"


def test_a_store_that_moved_during_the_check_is_409(rig):

    def moving(store):
        res = _check(store)
        _ctx(rig)["modifier"].set_value("qubits.q2.z.operations.const.amplitude", 0.2)
        return res
    ANSWER["fn"] = moving
    r = _ma(rig)
    assert r.status_code == 409 and r.get_json().get("stale")
    assert _merged(rig)["qubit_pairs"]["q1-q2"]["macros"]["cz"] == "#./cz_two"


def test_not_a_gate_and_the_alias_itself_are_refused(rig):
    assert _ma(rig, gate="cz_none").status_code == 400
    assert _ma(rig, gate="cz").status_code == 400


def test_htmx_make_active_answers_the_status_line(rig):
    r = rig.post("/api/pulse/cz-gate/make-active", data={"pair": "q1-q2", "gate": "cz_sq"},
                 headers={"HX-Request": "true"})
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'class="gate-status"' in body and ">active<" in body
    assert "gate-status-activate" not in body.split("pending-tray")[0]


def test_status_partial(rig):
    r = rig.get("/pulse/cz-gate/status?pair=q1-q2&gate=cz_sq")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    for text in ("on the chip", "not active", "not a class SM made",
                 "amplitude scaling: not known", "no check record"):
        assert text in body, text            # every item is in the details
    pills = body.split('class="gate-status-items"')[1].split("</span></span>")[0]
    assert "on the chip" in pills and "not active" not in pills
    assert "not a class SM made" not in pills and "no check record" not in pills
    assert "Make active" in body and ">Delete<" in body and "Edit values" in body
    assert 'role="group"' not in body
    assert "paths=qubit_pairs.q1-q2.macros.cz_sq.flux_pulse_qubit" in body
    r = rig.get("/pulse/cz-gate/status?pair=q1-q2&gate=cz_two")
    assert "Make active" not in r.get_data(as_text=True)


def test_status_says_only_in_the_working_copy_for_a_new_gate(rig):
    m = _ctx(rig)["modifier"]
    s, plan = add_gate(_merged(rig), name="cz_new")
    for path, value in plan["creates"]:
        m.create_subtree(path, value)
    body = rig.get("/pulse/cz-gate/status?pair=q1-q2&gate=cz_new").get_data(as_text=True)
    assert "only in the working copy" in body


def test_pair_detail_shows_every_gate_line(rig):
    body = rig.get("/pair/q1-q2", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert body.count('class="gate-status"') == 2
    assert 'data-gate="cz_sq"' in body and 'data-gate="cz_two"' in body


def test_pulse_detail_of_a_gate_pulse_shows_its_line(rig):
    body = rig.get("/pulse/detail?path=qubit_pairs.q1-q2.macros.cz_sq.flux_pulse_qubit",
                   headers={"HX-Request": "true"}).get_data(as_text=True)
    assert 'class="gate-status"' in body and 'data-gate="cz_sq"' in body


def test_delete_offer_lists_the_set(rig):
    body = rig.get("/pulse/cz-gate/delete-offer?pair=q1-q2&gate=cz_two").get_data(as_text=True)
    assert "qubits.q1.z.operations.cz_two_pulse_q1_q2" in body
    assert "has no active CZ gate" in body and "Delete the active gate" in body


def test_make_active_on_the_active_gate_is_refused_before_anything(rig):
    st = _ctx(rig)["store"]
    seq, n = st.mutation_seq, len(st.change_log)
    r = _ma(rig, gate="cz_two")
    assert r.status_code == 409
    assert r.get_json()["error"] == "cz_two is already the active CZ gate of q1-q2."
    assert rig._calls == [] and st.mutation_seq == seq and len(st.change_log) == n


@pytest.mark.parametrize("q", ["pair=nope&gate=cz_sq", "pair=q1-q2&gate=nope",
                               "pair=q1-q2&gate=cz", "pair=q1-q2", "gate=cz_sq", "",
                               "pair=q1-q2&gate=swap"])
def test_status_and_offer_refuse_a_gate_that_is_not_there(rig, q):
    _ctx(rig)["modifier"].create_subtree("qubit_pairs.q1-q2.macros.swap", {"__class__": "lab.Swap"})
    for url in ("/pulse/cz-gate/status?", "/pulse/cz-gate/delete-offer?"):
        r = rig.get(url + q)
        assert 400 <= r.status_code < 500, (url, q)
        body = r.get_data(as_text=True)
        assert "Make active" not in body and "Delete" not in body and "hx-post" not in body


def test_a_record_that_cannot_be_saved_is_said(rig, monkeypatch):
    monkeypatch.setattr(log, "chip_dir", lambda hm, path: (_ for _ in ()).throw(OSError("no")))
    r = _ma(rig)
    assert r.status_code == 200
    assert r.get_json()["record_error"]
    assert _merged(rig)["qubit_pairs"]["q1-q2"]["macros"]["cz"] == "#./cz_sq"


def test_pair_detail_head_says_no_active_gate(rig):
    _ctx(rig)["modifier"].delete_subtree(ALIAS)
    body = rig.get("/pair/q1-q2", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert "No active CZ gate (the pair's cz alias is not set)" in body
    assert body.count('data-status-key="active"') == 2   # details only, no pill
    assert "ui-pill--accent" not in body.split("gate-status-list")[1].split("</section>")[0]
