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
    body = body.replace("<wbr>", "")  # pointer lines may break after each dot; the text is unchanged
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


def test_a_pair_with_no_gate_keeps_the_zone_and_its_create_door(rig):
    """The pair page is where a user looks for the pair's gates: with none, the
    zone stays (quiet) and still carries the one button that creates the first."""
    mod = _ctx(rig)["modifier"]
    macros = _merged(rig)["qubit_pairs"]["q1-q2"]["macros"]
    for name in [k for k in macros]:
        mod.delete_subtree(f"qubit_pairs.q1-q2.macros.{name}")
    body = rig.get("/pair/q1-q2", headers={"HX-Request": "true"}).get_data(as_text=True)
    zone = body.split('aria-label="CZ gates"')[1].split("</section>")[0]
    assert "ui-zone--quiet" in body.split('aria-label="CZ gates"')[0].rsplit("<section", 1)[1]
    assert 'data-czg-open data-czg-pair="q1-q2"' in zone
    assert "No CZ gate on this pair yet." in zone
    assert '<span class="ui-count">0</span>' in zone and "no active gate" not in zone


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
    from urllib.parse import quote
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


# -- the pair detail's zone after Delete gate ---------------------------------

HX = {"HX-Request": "true"}


def _del_list(c, gate, **extra):
    return c.post("/api/pulse/delete-together", headers=HX,
                  data={"pair": "q1-q2", "gate": gate, "scope": "list", **extra})


def _rows(body):
    import re
    return re.findall(r'class="gate-status" data-gate-pair="q1-q2" data-gate="([^"]+)"', body)


def test_zone_delete_answers_the_true_list_with_the_notice_outside_the_rows(rig):
    r = _del_list(rig, "cz_sq")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert r.headers["HX-Retarget"] == "closest .gate-status-list"
    assert r.headers["HX-Reswap"] == "outerHTML"
    assert _rows(body) == ["cz_two"]
    assert "gate=cz_sq" not in body and '"gate": "cz_sq"' not in body
    assert '<span class="ui-count">1</span>' in body and "no active gate" not in body
    notice = body.split('class="gate-status-notice"')[1]
    assert "Deleted cz_sq and" in notice and "Ctrl+Z brings them back." in notice
    assert body.index("gate-status-notice") < body.index('class="gate-status"')


def test_zone_delete_of_the_active_and_last_gate(rig):
    r = _del_list(rig, "cz_two", confirm_active="1")
    body = r.get_data(as_text=True)
    assert _rows(body) == ["cz_sq"] and "no active gate" in body
    body = _del_list(rig, "cz_sq").get_data(as_text=True)
    assert _rows(body) == [] and "No CZ gate on this pair yet" in body
    assert "Deleted cz_sq and" in body and "ui-zone--quiet" in body


def test_delete_wording_counts_parts_without_brackets(rig):
    from quam_state_manager.web.gate_status_routes import parts_text
    assert parts_text(1) == "1 part" and parts_text(2) == "2 parts"
    body = _del_list(rig, "cz_sq").get_data(as_text=True)
    assert "(s)" not in body
    body = rig.post("/api/pulse/delete-together", headers=HX,
                    data={"pair": "q1-q2", "gate": "cz_two", "confirm_active": "1"}
                    ).get_data(as_text=True)
    assert "Deleted cz_two and" in body and "(s)" not in body


def test_undo_of_a_zone_delete_brings_the_row_back_through_the_list_route(rig):
    zone = rig.get("/pair/q1-q2", headers=HX).get_data(as_text=True)
    head = zone.split('<section class="ui-zone gate-status-list')[1].split(">")[0]
    assert 'hx-get="/pulse/cz-gate/list?pair=q1-q2"' in head
    assert "cellsReverted from:document" in head and 'hx-swap="outerHTML"' in head
    _del_list(rig, "cz_sq")
    assert _rows(rig.get("/pulse/cz-gate/list?pair=q1-q2").get_data(as_text=True)) == ["cz_two"]
    assert rig.post("/undo").status_code == 200
    assert _rows(rig.get("/pulse/cz-gate/list?pair=q1-q2").get_data(as_text=True)) == ["cz_sq", "cz_two"]


def test_second_delete_of_a_gone_gate_says_so_and_refreshes(rig):
    _del_list(rig, "cz_sq")
    # the other window's row Delete: the offer
    zone = rig.get("/pair/q1-q2", headers=HX).get_data(as_text=True)
    assert "scope=list" in zone
    r = rig.get("/pulse/cz-gate/delete-offer?pair=q1-q2&gate=cz_sq&scope=list", headers=HX)
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and r.headers["HX-Retarget"] == "closest .gate-status-list"
    assert "cz_sq is no longer on pair q1-q2" in body and _rows(body) == ["cz_two"]
    # the other window's open offer: its confirm
    st = _ctx(rig)["store"]
    n = len(st.change_log)
    r = _del_list(rig, "cz_sq")
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "cz_sq is no longer on pair q1-q2" in body
    assert "This gate is gone." in body and "Another window" not in body
    assert _rows(body) == ["cz_two"] and len(st.change_log) == n


def test_failed_zone_delete_keeps_the_row_with_the_error(rig, monkeypatch):
    from flask import jsonify
    from quam_state_manager.web import routes as R
    monkeypatch.setattr(R, "_field_edit_batch_impl",
                        lambda payload, pulse_door=False: (jsonify(ok=False, error="boom"), 400))
    st = _ctx(rig)["store"]
    before = json.dumps(st.state)
    body = _del_list(rig, "cz_sq").get_data(as_text=True)
    assert _rows(body) == ["cz_sq", "cz_two"] and "boom" in body
    row = body.split('data-gate="cz_sq"')[1].split('data-gate="cz_two"')[0]
    assert "ui-fail-row" in row and "boom" in row
    assert "gate-status-notice" not in body and json.dumps(st.state) == before


def test_offer_from_the_zone_carries_the_list_scope(rig):
    body = rig.get("/pulse/cz-gate/delete-offer?pair=q1-q2&gate=cz_sq&scope=list").get_data(as_text=True)
    assert '&#34;scope&#34;: &#34;list&#34;' in body or '"scope": "list"' in body
    body = rig.get("/pulse/cz-gate/delete-offer?pair=q1-q2&gate=cz_sq").get_data(as_text=True)
    assert '"scope": "list"' not in body and '&#34;scope&#34;: &#34;list&#34;' not in body


def test_empty_zone_is_one_row_tall():
    from pathlib import Path
    css = (Path(__file__).parent.parent / "quam_state_manager/web/static/ui-kit.css").read_text(encoding="utf-8")
    rule = css.split(".gate-status-list.ui-zone--quiet > .ui-zone-body {")[1].split("}")[0]
    assert "min-height: 0" in rule


HOLDER = "qubit_pairs.q1-q2.macros.other_gate.flux_pulse_target"


def _by_name_holder(monkeypatch):
    from quam_state_manager.web import routes as R
    monkeypatch.setattr(R, "_by_name_cut", lambda store, paths: [HOLDER])


def test_zone_by_name_refusal_names_the_real_next_step(rig, monkeypatch):
    _by_name_holder(monkeypatch)
    n = len(_ctx(rig)["store"].change_log)
    r = _del_list(rig, "cz_sq")
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and r.headers.get("HX-Retarget") == "closest .gate-status-list"
    row = body.split('data-gate="cz_sq"')[1].split('data-gate="cz_two"')[0]
    assert HOLDER in row and "cannot be deleted while a slot uses it" in row
    assert "Json Tree" in row and "confirm step" not in body and "anyway" not in body
    assert _rows(body) == ["cz_sq", "cz_two"] and len(_ctx(rig)["store"].change_log) == n


def test_pulse_detail_by_name_refusal_is_the_json_it_was(rig, monkeypatch):
    """No scope (the pulse detail's offer), htmx or not: the very answer the door
    gave before the zone existed, the shared 409 JSON, byte for byte."""
    from quam_state_manager.web import routes as R
    _by_name_holder(monkeypatch)
    with rig._app.test_request_context():
        want, code = R._by_name_refused_json([HOLDER])
        want = want.get_data()
    for headers in (HX, {}):
        r = rig.post("/api/pulse/delete-together", headers=headers,
                     data={"pair": "q1-q2", "gate": "cz_sq"})
        assert (r.status_code, r.get_data()) == (code, want)
        assert "HX-Retarget" not in r.headers


def test_failed_zone_delete_leaves_the_working_copy_file_untouched(rig, monkeypatch):
    """The real batch runs; its second delete fails, so the first one is rolled
    back. The working copy on disk is byte-identical afterwards."""
    import hashlib
    from quam_state_manager.core import modifier as M
    st = _ctx(rig)["store"]
    folder = st.folder_path

    def md5s():
        return {p.name: hashlib.md5(p.read_bytes()).hexdigest()
                for p in sorted(folder.iterdir()) if p.is_file()}
    before, before_state = md5s(), json.dumps(st.state, sort_keys=True)
    real, calls = M.Modifier.delete_subtree, []

    def second_fails(self, dot_path, **kw):
        calls.append(dot_path)
        if len(calls) == 2:
            raise KeyError(dot_path)
        return real(self, dot_path, **kw)
    monkeypatch.setattr(M.Modifier, "delete_subtree", second_fails)
    body = _del_list(rig, "cz_sq").get_data(as_text=True)
    assert len(calls) >= 2 and _rows(body) == ["cz_sq", "cz_two"]
    assert "ui-fail-row" in body.split('data-gate="cz_sq"')[1].split('data-gate="cz_two"')[0]
    assert md5s() == before and json.dumps(st.state, sort_keys=True) == before_state


@pytest.mark.parametrize("q", ["", "?pair=", "?pair=nope", "?pair=q1-q2%22%3E"])
def test_list_route_answers_nothing_for_a_missing_or_unknown_pair(rig, q):
    r = rig.get("/pulse/cz-gate/list" + q)
    assert r.status_code == 204 and r.get_data() == b""


def test_list_route_urlencodes_an_odd_pair_name(tmp_path):
    s, _ = add_gate(chip())
    s["qubit_pairs"]['q1 & "q2"'] = s["qubit_pairs"].pop("q1-q2")
    folder = write(tmp_path / "chip", s)
    c = create_app(testing=True, instance_path=str(tmp_path / "inst")).test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    r = c.get("/pulse/cz-gate/list?pair=q1%20%26%20%22q2%22")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert 'hx-get="/pulse/cz-gate/list?pair=q1%20%26%20%22q2%22"' in body
    assert 'data-gate-pair="q1 &amp; &#34;q2&#34;"' in body or 'data-gate-pair="q1 &amp; &quot;q2&quot;"' in body


def test_read_only_chip_list_offers_no_live_delete(rig):
    """An archive (read-only) chip: neither the zone's offer nor its confirm
    removes anything; both say why in readable words, on disk nothing moves."""
    from quam_state_manager.web.gate_status_routes import ARCHIVE_REFUSAL
    ctx = _ctx(rig)
    ctx["origin"] = "archive"
    st = ctx["store"]
    before, n = json.dumps(st.state), len(st.change_log)
    off = rig.get("/pulse/cz-gate/delete-offer?pair=q1-q2&gate=cz_sq&scope=list", headers=HX)
    r = _del_list(rig, "cz_sq")
    body = r.get_data(as_text=True)
    assert ARCHIVE_REFUSAL in body and "deleted" not in body.lower().replace("not deleted", "")
    assert json.dumps(st.state) == before and len(st.change_log) == n
    assert off.status_code < 500


def test_zone_refreshes_on_undo_and_on_a_sync_pull(rig):
    body = rig.get("/pulse/cz-gate/list?pair=q1-q2").get_data(as_text=True)
    trig = body.split('hx-trigger="')[1].split('"')[0]
    assert {t.strip() for t in trig.split(",")} == {
        "pulses-changed from:body", "cellsReverted from:document", "stateRestored from:document",
        "gate-list-refresh"}


def test_the_gate_zone_is_its_own_request_slot_not_the_inspector_panes(rig):
    """The pair detail sits in #inspector-pane (hx-sync="this:replace"). Without a
    sync of its own, a row press and the zone's refresh shared that slot and the
    later one aborted the earlier, which htmx logs to the console."""
    zone = rig.get("/pair/q1-q2", headers=HX).get_data(as_text=True)
    head = zone.split('<section class="ui-zone gate-status-list')[1].split(">")[0]
    sync = head.split('hx-sync="')[1].split('"')[0] if 'hx-sync="' in head else ""
    assert sync.split(":")[0] == "this", head


def test_every_press_button_of_a_gate_row_is_its_own_request_slot():
    """A press button without a sync of its own inherits the inspector pane's
    replace slot: a zone refresh arriving with the press then aborts the press and the
    user has to press again. Edit values is the one navigation and stays in the pane's slot."""
    import re
    from pathlib import Path
    root = Path(__file__).parent.parent / "quam_state_manager/web/templates"
    seen = []
    for name in ("_gate_status.html", "_gate_delete_offer.html"):
        text = (root / name).read_text(encoding="utf-8")
        for tag in re.findall(r"<button\s[^>]*>", text, flags=re.S):
            cls = re.search(r'class="([^"]*)"', tag).group(1)
            seen.append(cls)
            sync = re.search(r'hx-sync="([^"]*)"', tag)
            if "gate-status-edit" in cls.split():
                assert sync is None or not sync.group(1).startswith("this:"), tag
            else:
                assert sync is not None and sync.group(1) == "this:drop", (name, tag)
    for want in ("gate-status-recheck", "gate-status-activate", "gate-status-delete",
                 "gate-delete-confirm", "gate-delete-cancel", "gate-status-edit"):
        assert any(want in c.split() for c in seen), want


def _zone_of(body):
    start = body.index('<section class="ui-zone gate-status-list')
    return body[start:body.index("</section>", start) + len("</section>")]


def test_a_press_and_the_zone_refresh_both_reach_the_server_under_real_htmx(rig, tmp_path, monkeypatch):
    """Behaviour under the bundled htmx (tests/gate_zone_sync_selfcheck.cjs) with the real
    server answers: one press at a time, one refresh after each press, no press lost, no abort."""
    import os
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if not node or not os.environ.get("NODE_PATH"):
        pytest.skip("node + jsdom (NODE_PATH) needed")
    zone = _zone_of(rig.get("/pair/q1-q2", headers=HX).get_data(as_text=True))
    r = rig.post("/api/pulse/cz-gate/make-active", headers=HX,
                 data={"pair": "q1-q2", "gate": "cz_sq", "scope": "list"})
    assert r.status_code == 200
    activate = {"body": r.get_data(as_text=True), "headers": dict(r.headers)}
    assert rig.post("/undo").status_code == 200

    def ans(r):
        assert r.status_code == 200
        return {"body": r.get_data(as_text=True), "headers": dict(r.headers)}
    ma = {"pair": "q1-q2", "scope": "list"}
    refuse = ans(rig.post("/api/pulse/cz-gate/make-active", headers=HX, data={**ma, "gate": "cz_two"}))
    assert "already the active CZ gate" in refuse["body"]

    def no_record(*a, **k):
        raise OSError("disk full")
    keep = log.record_check
    monkeypatch.setattr(log, "record_check", no_record)
    warn = ans(rig.post("/api/pulse/cz-gate/make-active", headers=HX, data={**ma, "gate": "cz_sq"}))
    assert "check record could not be saved" in warn["body"]
    monkeypatch.setattr(log, "record_check", keep)
    assert rig.post("/undo").status_code == 200
    delrefuse = ans(_del_list(rig, "cz_two"))
    assert "is the active CZ gate of pair" in delrefuse["body"]
    offer = ans(rig.get("/pulse/cz-gate/delete-offer?pair=q1-q2&gate=cz_two&scope=list", headers=HX))
    assert "gate-delete-confirm" in offer["body"]
    slotfail = ans(rig.post("/api/pulse/delete-together", headers=HX, data={"pair": "q1-q2", "gate": "cz_two"}))
    assert "ui-fail-row" in slotfail["body"]
    full = ans(rig.get("/pulse/cz-gate/list?pair=q1-q2", headers=HX))
    with monkeypatch.context() as m:
        _state_notices(m)
        noticed = ans(rig.get("/pulse/cz-gate/list?pair=q1-q2", headers=HX))
    row = ans(rig.get("/pulse/cz-gate/status?pair=q1-q2&gate=cz_sq", headers=HX))
    ANSWER["fn"] = _edit_during_check(rig, lambda store: _check(store, fail="The preview differs."))
    stale = ans(rig.post("/api/pulse/cz-gate/make-active", headers=HX, data={**ma, "gate": "cz_sq"}))
    stale_row = ans(rig.post("/api/pulse/cz-gate/make-active", headers=HX,
                             data={"pair": "q1-q2", "gate": "cz_sq"}))
    assert all("changed while the gate was checked" in a["body"] for a in (stale, stale_row))
    ANSWER["fn"] = lambda store: _check(store)
    deleted = ans(_del_list(rig, "cz_sq"))       # another window deletes cz_sq
    assert "cz_sq deleted" in deleted["body"]
    listed = rig.get("/pulse/cz-gate/list?pair=q1-q2", headers=HX)
    gone = rig.get("/pulse/cz-gate/delete-offer?pair=q1-q2&gate=cz_sq&scope=list", headers=HX)
    assert "This gate is gone." in gone.get_data(as_text=True)
    fixture = {"zone": zone, "activate": activate, "row": row, "stale": stale, "stale_row": stale_row,
               "list": {"body": listed.get_data(as_text=True), "headers": dict(listed.headers)},
               "gone": {"body": gone.get_data(as_text=True), "headers": dict(gone.headers)},
               "refuse": refuse, "warn": warn, "delrefuse": delrefuse, "offer": offer,
               "slotfail": slotfail, "full": full, "noticed": noticed, "deleted": deleted}
    path = tmp_path / "gate_zone_sync.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    p = subprocess.run([node, str(Path(__file__).parent / "gate_zone_sync_selfcheck.cjs"), str(path)],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "ALL OK" in p.stdout


def _after_swap_events(r):
    raw = r.headers.get("HX-Trigger-After-Swap")
    return json.loads(raw) if raw else {}


def test_a_delete_names_its_gate_for_the_pair_page_and_a_refusal_does_not(rig):
    """The delete answer (gate list and single row) carries the event the pair page drops the
    gate's parameter zone on; a refused delete carries none."""
    refused = _del_list(rig, "cz_two")
    assert "is the active CZ gate of pair" in refused.get_data(as_text=True)
    assert "gate-deleted" not in _after_swap_events(refused)
    r = _del_list(rig, "cz_sq")
    assert "cz_sq deleted" in r.get_data(as_text=True)
    assert _after_swap_events(r) == {"gate-deleted": {"pair": "q1-q2", "gate": "cz_sq"}}
    assert rig.post("/undo").status_code == 200
    r = rig.post("/api/pulse/delete-together", headers=HX, data={"pair": "q1-q2", "gate": "cz_sq"})
    assert r.status_code == 200 and "Deleted cz_sq" in r.get_data(as_text=True)
    assert _after_swap_events(r) == {"gate-deleted": {"pair": "q1-q2", "gate": "cz_sq"}}


def test_a_deleted_gate_zone_leaves_the_pair_page_and_its_undo_brings_it_back(rig, tmp_path):
    """Behaviour under the bundled htmx (tests/gate_delete_zone_selfcheck.cjs) with the real
    server answers: a delete takes its gate's zone off the pair page and only that one, a
    refusal leaves every zone, the undo of the delete draws the page with the zone again."""
    import os
    import re
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if not node or not os.environ.get("NODE_PATH"):
        pytest.skip("node + jsdom (NODE_PATH) needed")

    def ans(r):
        assert r.status_code == 200
        return {"body": r.get_data(as_text=True), "headers": dict(r.headers)}
    page = ans(rig.get("/pair/q1-q2", headers=HX))
    assert re.findall(r'data-detail-zone="([^"]+)"', page["body"]) == ["cz_sq", "cz_two"]
    offer = ans(rig.get("/pulse/cz-gate/delete-offer?pair=q1-q2&gate=cz_sq&scope=list", headers=HX))
    offer_other = ans(rig.get("/pulse/cz-gate/delete-offer?pair=q1-q2&gate=cz_two&scope=list",
                              headers=HX))
    refused_text = "is the active CZ gate of pair"
    refused = ans(_del_list(rig, "cz_two"))
    assert refused_text in refused["body"]
    deleted = ans(_del_list(rig, "cz_sq"))
    undo = rig.post("/undo", headers=HX)
    assert undo.status_code == 200
    after = ans(rig.get("/pair/q1-q2", headers=HX))
    fixture = {"pair": "q1-q2", "gates": ["cz_sq", "cz_two"], "page": page, "offer": offer,
               "offer_other": offer_other, "refused": refused, "refused_text": refused_text,
               "deleted": deleted, "after": after,
               "undo": json.loads(undo.headers["HX-Trigger"])["cellsReverted"]}
    path = tmp_path / "gate_delete_zone.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    p = subprocess.run([node, str(Path(__file__).parent / "gate_delete_zone_selfcheck.cjs"), str(path)],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "ALL OK" in p.stdout


def _state_notices(m):
    """Every render of the zone shows the chip-state notices: the Re-check notice and a
    row's class-file line."""
    from quam_state_manager.web import gate_status_routes as gsr
    m.setattr(gsr, "recheck_notice", lambda store, ctx: {
        "tone": "warn", "title": "Gates wait for Re-check", "text": "The class file changed.",
        "gates": ["cz_sq"]})
    keep = gsr.gate_line

    def line(*a, **k):
        out = keep(*a, **k)
        out["notices"] = [{"key": "needs", "text": "needs the class file"}]
        return out
    m.setattr(gsr, "gate_line", line)


def test_only_press_answers_carry_the_press_answer_mark(rig, monkeypatch):
    """A plain zone render (also with every chip-state notice) has no data-press-answer;
    each press answer of the zone has it on its message, and only there."""
    with monkeypatch.context() as m:
        _state_notices(m)
        plain = rig.get("/pulse/cz-gate/list?pair=q1-q2", headers=HX).get_data(as_text=True)
        assert "data-recheck-notice" in plain and 'data-status-key="needs"' in plain
        assert "data-press-answer" not in plain
        pair_page = _zone_of(rig.get("/pair/q1-q2", headers=HX).get_data(as_text=True))
        assert "data-press-answer" not in pair_page
    ma = {"pair": "q1-q2", "scope": "list"}

    def marked(body, says):
        assert body.count("data-press-answer") == 1, body
        part = body.split("data-press-answer", 1)[1]
        assert says in part.split('class="gate-status"', 1)[0], part[:400]
    marked(rig.post("/api/pulse/cz-gate/make-active", headers=HX,
                    data={**ma, "gate": "cz_two"}).get_data(as_text=True), "already the active CZ gate")
    marked(_del_list(rig, "cz_two").get_data(as_text=True), "is the active CZ gate of pair")
    marked(_del_list(rig, "cz_sq").get_data(as_text=True), "cz_sq deleted")
    marked(rig.get("/pulse/cz-gate/delete-offer?pair=q1-q2&gate=cz_sq&scope=list",
                   headers=HX).get_data(as_text=True), "This gate is gone.")


def _edit_during_check(rig, check_answer):
    """A check whose answer is *check_answer*, with an edit landing while it runs."""
    def moving(store):
        res = check_answer(store)
        _ctx(rig)["modifier"].set_value("qubits.q2.z.operations.const.amplitude", 0.2)
        return res
    return moving


def test_an_edit_during_the_check_is_named_before_the_checks_verdict(rig):
    """The check saw the gate as it was before the edit: its failure is not this gate's."""
    msg = "The preview differs from the config. 136 preview samples, 120 in the config."
    ANSWER["fn"] = _edit_during_check(rig, lambda store: _check(store, fail=msg))
    r = _ma(rig)
    err = r.get_json()["error"]
    assert r.status_code == 409 and r.get_json().get("stale"), err
    assert "changed while the gate was checked" in err and "lab's code" not in err
    assert _merged(rig)["qubit_pairs"]["q1-q2"]["macros"]["cz"] == "#./cz_two"


def test_an_edit_during_an_unrun_check_is_named_before_the_not_checked_refusal(rig):
    _ctx(rig)["modifier"].set_value("qubit_pairs.q1-q2.macros.cz_sq.__class__", "lab.g.MyCZ")
    ANSWER["fn"] = _edit_during_check(
        rig, lambda store: _check(store, all_checked=False, status="no-env"))
    r = _ma(rig)
    assert r.status_code == 409 and "changed while the gate was checked" in r.get_json()["error"]


def test_an_unchanged_chip_still_names_the_lab_code_cause_once(rig):
    msg = "The preview differs from the config. 136 preview samples, 120 in the config."
    ANSWER["fn"] = lambda store: _check(store, fail=msg)
    r = _ma(rig)
    err = r.get_json()["error"]
    assert r.status_code == 400 and err.startswith("The gate does not run with the lab's code: ")
    assert "120 in the config. It was not made active." in err and ".." not in err


def test_htmx_an_edit_during_the_check_shows_the_stale_note_on_the_line(rig):
    ANSWER["fn"] = _edit_during_check(rig, lambda store: _check(store, fail="The preview differs."))
    r = rig.post("/api/pulse/cz-gate/make-active", headers=HX, data={"pair": "q1-q2", "gate": "cz_sq"})
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "changed while the gate was checked" in body
    assert "does not run with" not in body


def test_a_gate_deleted_from_its_pulse_detail_leaves_no_live_looking_detail(rig, tmp_path):
    """Behaviour under the bundled htmx (tests/gate_single_delete_selfcheck.cjs) with the real
    server answers: a gate deleted from the status line of its pulse detail leaves a detail
    that says the gate was deleted and offers nothing to edit; the undo of the delete draws
    the gate's pulses again, an undo that does not bring the gate back draws nothing; the
    redo of the delete puts the "deleted" detail back and a further undo reopens it."""
    import html
    import os
    import re
    import shutil
    import subprocess
    from pathlib import Path
    from urllib.parse import quote
    node = shutil.which("node")
    if not node or not os.environ.get("NODE_PATH"):
        pytest.skip("node + jsdom (NODE_PATH) needed")

    def ans(r):
        assert r.status_code == 200
        return {"body": r.get_data(as_text=True), "headers": dict(r.headers)}
    rows = rig.get("/pulse/cz-gate/list?pair=q1-q2", headers=HX).get_data(as_text=True)
    row = rows.split('data-gate="cz_sq"', 1)[1]
    url = html.unescape(re.search(r'hx-get="(/pulse/detail\?[^"]+)"', row).group(1))
    detail = ans(rig.get(url, headers=HX))
    assert 'class="gate-status" data-gate-pair="q1-q2" data-gate="cz_sq"' in detail["body"]
    offer = ans(rig.get("/pulse/cz-gate/delete-offer?pair=q1-q2&gate=cz_sq", headers=HX))
    deleted = ans(rig.post("/api/pulse/delete-together", headers=HX,
                           data={"pair": "q1-q2", "gate": "cz_sq"}))
    assert deleted["headers"]["HX-Retarget"] == "closest #pulse-detail-root"
    assert deleted["headers"]["HX-Reswap"] == "outerHTML"
    undo = rig.post("/undo", headers=HX)
    assert undo.status_code == 200
    again = ans(rig.get(url, headers=HX))
    # the redo takes the gate away again: its answer names the macro path, flagged as gone now
    redo = rig.post("/redo", headers=HX)
    assert redo.status_code == 200
    redo_rev = json.loads(redo.headers["HX-Trigger"])["cellsReverted"]
    macro = "qubit_pairs.q1-q2.macros.cz_sq"
    assert any(e["dot_path"] == macro and e["created"] for e in redo_rev["entries"]), redo_rev
    view = json.loads(html.unescape(re.search(r"data-view-paths='([^']*)'", again["body"]).group(1)))
    assert view
    gone_url = ("/pulse/cz-gate/deleted-detail?pair=q1-q2&gate=cz_sq"
                + "".join("&paths=" + quote(p, safe="") for p in view))
    gone = ans(rig.get(gone_url, headers=HX))
    assert "cz_sq" in gone["body"] and "data-gate-gone" in gone["body"]
    reopen = html.unescape(re.search(r'data-reopen="([^"]*)"', gone["body"]).group(1))
    undo2 = rig.post("/undo", headers=HX)
    assert undo2.status_code == 200
    again2 = ans(rig.get(reopen, headers=HX))
    # while the gate is on the chip the gone detail answers nothing to swap
    assert rig.get(gone_url, headers=HX).status_code == 204
    fixture = {"pair": "q1-q2", "gate": "cz_sq", "url": url, "detail": detail, "offer": offer,
               "deleted": deleted, "again": again,
               "undo": json.loads(undo.headers["HX-Trigger"])["cellsReverted"],
               "redo": redo_rev, "gone_url": gone_url, "gone": gone, "reopen": reopen,
               "undo2": json.loads(undo2.headers["HX-Trigger"])["cellsReverted"], "again2": again2}
    path = tmp_path / "gate_single_delete.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    p = subprocess.run([node, str(Path(__file__).parent / "gate_single_delete_selfcheck.cjs"), str(path)],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "ALL OK" in p.stdout
