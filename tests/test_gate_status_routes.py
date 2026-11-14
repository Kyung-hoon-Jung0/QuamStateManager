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
    assert 'class="ui-text-warn" title="The pair' in body and '>no active gate</span>' in body
    assert body.count("no active gate") == 1
    assert body.count('data-status-key="active"') == 2   # details only, no pill
    assert "ui-pill--accent" not in body.split("gate-status-list")[1].split("</section>")[0]


def test_list_scope_make_active_updates_the_head_and_the_old_active_row(rig):
    _ctx(rig)["modifier"].delete_subtree(ALIAS)
    hx = {"HX-Request": "true"}
    r = rig.post("/api/pulse/cz-gate/make-active", headers=hx,
                 data={"pair": "q1-q2", "gate": "cz_sq", "scope": "list"})
    body = r.get_data(as_text=True).split("pending-tray")[0]
    assert "gate-status-list" in body and "no active gate" not in body
    r = rig.post("/api/pulse/cz-gate/make-active", headers=hx,
                 data={"pair": "q1-q2", "gate": "cz_two", "scope": "list"})
    body = r.get_data(as_text=True).split("pending-tray")[0]
    rows = {seg.split('"')[0]: seg for seg in body.split('data-gate="')[1:]}
    assert "ui-pill--accent" in rows["cz_two"] and "ui-pill--accent" not in rows["cz_sq"]
    assert "gate-status-activate" in rows["cz_sq"] and "gate-status-activate" not in rows["cz_two"]
    assert body.count("ui-pill--accent") == 1
    assert rig.post("/undo").status_code == 200
    assert _merged(rig)["qubit_pairs"]["q1-q2"]["macros"]["cz"] == "#./cz_sq"


def test_the_list_buttons_ask_for_the_list(rig):
    body = rig.get("/pair/q1-q2", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert '&#34;scope&#34;: &#34;list&#34;' in body or '"scope": "list"' in body
    assert 'hx-target="closest .gate-status-list"' in body


def test_check_pill_is_short_and_the_reason_is_its_title(rig):
    hm = rig._app.config["history_manager"]
    folder = log.chip_dir(hm, _ctx(rig)["path"])
    log.record(folder, "q1-q2", "cz_sq", result=log.NOT_CHECKED,
               reason="no Python environment is selected",
               content=log.content_hash(_merged(rig), "q1-q2", "cz_sq"))
    body = rig.get("/pulse/cz-gate/status?pair=q1-q2&gate=cz_sq").get_data(as_text=True)
    pills = body.split('class="gate-status-items"')[1].split("</span></span>")[0]
    assert '>not checked' in pills and 'not checked:' not in pills
    assert "Not checked with the lab&#39;s code: no Python environment is selected" in pills
    details = body.split("gate-status-details-list")[1]
    assert details.index('data-status-key="check"') < details.index('data-status-key="where"')


def test_delete_offer_has_cancel(rig):
    body = rig.get("/pulse/cz-gate/delete-offer?pair=q1-q2&gate=cz_sq").get_data(as_text=True)
    assert "gate-delete-cancel" in body and ">Cancel<" in body


def test_gate_status_selfcheck():
    import os
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if not node or not os.environ.get("NODE_PATH"):
        pytest.skip("node + jsdom (NODE_PATH) needed")
    p = subprocess.run([node, str(Path(__file__).parent / "gate_status_selfcheck.cjs")],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "ALL OK" in p.stdout


def test_cancel_needs_no_script(rig):
    body = rig.get("/pulse/cz-gate/delete-offer?pair=q1-q2&gate=cz_sq").get_data(as_text=True)
    cancel = body.split("gate-delete-cancel")[1].split("</button>")[0]
    assert 'hx-get="/pulse/cz-gate/offer-close"' in cancel
    assert 'hx-target="closest .gate-status-slot"' in cancel and 'hx-swap="innerHTML"' in cancel
    st = _ctx(rig)["store"]
    seq = st.mutation_seq
    r = rig.get("/pulse/cz-gate/offer-close", headers={"HX-Request": "true"})
    assert r.status_code == 200 and r.get_data(as_text=True) == ""
    assert st.mutation_seq == seq


def test_refused_offer_close_needs_no_script(rig):
    _ctx(rig)["modifier"].create_subtree(
        "qubits.q2.amp_ref", "#/qubit_pairs/q1-q2/macros/cz_sq/flux_pulse_qubit/amplitude")
    body = rig.get("/pulse/cz-gate/delete-offer?pair=q1-q2&gate=cz_sq").get_data(as_text=True)
    assert "Cannot delete" in body and 'hx-get="/pulse/cz-gate/offer-close"' in body


def test_gate_delete_on_an_archive_is_refused(rig):
    from quam_state_manager.web.gate_status_routes import ARCHIVE_REFUSAL
    _ctx(rig)["origin"] = "archive"
    st = _ctx(rig)["store"]
    before, n = json.dumps(st.state), len(st.change_log)
    r = rig.post("/api/pulse/delete-together", json={"pair": "q1-q2", "gate": "cz_sq"})
    assert r.status_code == 409 and r.get_json()["error"] == ARCHIVE_REFUSAL
    assert json.dumps(st.state) == before and len(st.change_log) == n
    assert _ma(rig).get_json()["error"] == ARCHIVE_REFUSAL


def test_gate_delete_under_the_agent_lock_is_refused(rig, monkeypatch):
    from flask import jsonify
    from quam_state_manager.web import routes as R
    calls = []

    def locked(ctx):
        calls.append(1)
        return jsonify(ok=False, error="locked"), 423
    monkeypatch.setattr(R, "_agent_edit_lock_refusal", locked)
    st = _ctx(rig)["store"]
    before = json.dumps(st.state)
    r = rig.post("/api/pulse/delete-together", json={"pair": "q1-q2", "gate": "cz_sq"})
    assert r.status_code == 423 and calls
    assert json.dumps(st.state) == before


def test_list_make_active_of_a_missing_gate_is_4xx(rig):
    r = rig.post("/api/pulse/cz-gate/make-active", headers={"HX-Request": "true"},
                 data={"pair": "q1-q2", "gate": "cz_none", "scope": "list"})
    assert 400 <= r.status_code < 500
    assert "no CZ gate named cz_none" in r.get_data(as_text=True)


def test_a_type_only_rewrite_keeps_checked(rig):
    assert _ma(rig).status_code == 200
    m = _ctx(rig)["modifier"]
    p = "qubit_pairs.q1-q2.macros.cz_sq.flux_pulse_qubit.length"
    st = _ctx(rig)["store"]
    node = st.merged
    for seg in p.split(".")[:-1]:
        node = node[seg]
    node["length"] = float(node["length"])     # what a node's save may do
    body = rig.get("/pulse/cz-gate/status?pair=q1-q2&gate=cz_sq").get_data(as_text=True)
    pills = body.split('class="gate-status-items"')[1].split("</span></span>")[0]
    assert pills.endswith(">checked") or ">checked<" in pills


def test_a_row_whose_status_raises_still_renders(rig, monkeypatch):
    from quam_state_manager.core import gate_status as gs_mod
    real = gs_mod.status

    def flaky(store, ctx, pair, gate, **kw):
        if gate == "cz_sq":
            raise TypeError("can't compare offset-naive and offset-aware datetimes")
        return real(store, ctx, pair, gate, **kw)
    monkeypatch.setattr(gs_mod, "status", flaky)
    body = rig.get("/pair/q1-q2", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert 'data-gate="cz_sq"' in body and "status could not be read" in body
    assert 'data-gate="cz_two"' in body
    r = rig.get("/pulse/cz-gate/status?pair=q1-q2&gate=cz_sq")
    assert r.status_code == 200 and "status could not be read" in r.get_data(as_text=True)


def test_htmx_archive_refusals_render_a_readable_line(rig):
    from quam_state_manager.web.gate_status_routes import ARCHIVE_REFUSAL
    _ctx(rig)["origin"] = "archive"
    hx = {"HX-Request": "true"}
    r = rig.post("/api/pulse/cz-gate/make-active", headers=hx,
                 data={"pair": "q1-q2", "gate": "cz_sq", "scope": "list"})
    assert r.status_code == 200 and ARCHIVE_REFUSAL in r.get_data(as_text=True).replace("&#39;", "'")
    r = rig.post("/api/pulse/delete-together", headers=hx, data={"pair": "q1-q2", "gate": "cz_sq"})
    assert r.status_code == 200 and ARCHIVE_REFUSAL in r.get_data(as_text=True).replace("&#39;", "'")


def test_htmx_lock_refusals_render_a_readable_line(rig, monkeypatch):
    from flask import jsonify
    from quam_state_manager.web import routes as R
    monkeypatch.setattr(R, "_agent_edit_lock_refusal",
                        lambda ctx: (jsonify(ok=False, error="An agent plan holds this chip."), 423))
    hx = {"HX-Request": "true"}
    r = rig.post("/api/pulse/cz-gate/make-active", headers=hx,
                 data={"pair": "q1-q2", "gate": "cz_sq", "scope": "list"})
    assert r.status_code == 200 and "An agent plan holds this chip." in r.get_data(as_text=True)
    r = rig.post("/api/pulse/delete-together", headers=hx, data={"pair": "q1-q2", "gate": "cz_sq"})
    assert r.status_code == 200 and "An agent plan holds this chip." in r.get_data(as_text=True)


def test_htmx_refusal_on_a_row_whose_status_raises_is_not_a_500(rig, monkeypatch):
    from quam_state_manager.core import gate_status as gs_mod
    from quam_state_manager.web.gate_status_routes import ARCHIVE_REFUSAL
    monkeypatch.setattr(gs_mod, "status", lambda *a, **k: (_ for _ in ()).throw(TypeError("x")))
    _ctx(rig)["origin"] = "archive"
    r = rig.post("/api/pulse/cz-gate/make-active", headers={"HX-Request": "true"},
                 data={"pair": "q1-q2", "gate": "cz_sq"})
    body = r.get_data(as_text=True).replace("&#39;", "'")
    assert r.status_code == 200
    assert "status could not be read" in body and ARCHIVE_REFUSAL in body
