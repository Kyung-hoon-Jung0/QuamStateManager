"""Recommend core layout on a loaded chip: the per-FEM recommendation (the
wizard's own pinning rule run over the raw state), its gating and panel line,
and the staged apply through /diagnostics/apply-fix."""
from __future__ import annotations

import copy
import json

import pytest

from quam_state_manager.core import core_layout as cl
from quam_state_manager.web.app import create_app
from tests.test_core_layout import Chip


def _null_chip(n=5):
    c = Chip()
    for i in range(1, n + 1):
        c.mw_qubit(f"q{i}")
    return c


def _rec(state, con="con1", fem=1):
    return cl.recommend_layout(state)["fems"][(con, fem)]


def _input_only(c, name, fem=1):
    """An element with only an MW input: its one term (readout processes) is estimated."""
    c.s["qubits"].setdefault(name, {})["probe"] = {
        "core": None, "thread": None, "opx_input": c.port("mw_inputs", "con1", fem)}


# --- the recommendation ---------------------------------------------------------
def test_five_null_qubits_get_layout_a_and_a_button():
    s = _null_chip().s
    p = _rec(s)
    assert p["button"] is True and p["panel_line"] is None
    assert p["cores"] == {"limit": 8, "before": 10.0, "after": 5.0, "firm_after": 5.0, "estimated": False}
    assert sorted(f["dot_path"] for f in p["fixes"]) == sorted(
        f"qubits.q{i}.{r}.core" for i in range(1, 6) for r in ("xy", "resonator"))
    assert {(f["action"], f["value"]) for f in p["fixes"] if f["dot_path"].startswith("qubits.q3.")} == {
        ("set_value", "q3_core")}
    assert [g["core"] for g in p["groups_after"]] == [f"q{i}_core" for i in range(1, 6)]
    assert {c["element"] for c in p["changes"]} == {f["dot_path"][:-5] for f in p["fixes"]}
    fd = [f for f in cl.surface_findings(s, "load_check") if f.values.get("kind") == "fem_budget"][0]
    assert fd.detail.split("\n")[1] == cl.FIX_RECOMMEND
    assert fd.fixes == p["fixes"] and fd.preview["button"] is True and "fixes" not in fd.preview
    assert fd.as_dict()["preview"]["cores"]["after"] == 5.0


def test_review_never_carries_the_button():
    rv = [f for f in cl.surface_findings(_null_chip().s, "review") if f.values.get("kind") == "fem_budget"][0]
    assert rv.fixes is None and rv.preview is None
    assert cl.finding_line(rv)["fixes"] == [] and cl.finding_line(rv)["preview"] is None


def test_thread_only_element_creates_core_and_nulls_thread():
    c = _null_chip()
    xy = c.s["qubits"]["q2"]["xy"]
    del xy["core"]
    xy["thread"] = "q2"
    p = _rec(c.s)
    q2 = [f for f in p["fixes"] if f["dot_path"].startswith("qubits.q2.xy.")]
    assert q2 == [{"action": "create_value", "dot_path": "qubits.q2.xy.core", "value": "q2_core"},
                  {"action": "set_value", "dot_path": "qubits.q2.xy.thread", "value": "null"}]
    assert {"element": "qubits.q2.xy", "key": "thread", "before": "q2", "after": None} in p["changes"]


def test_an_element_left_on_a_replaced_name_is_listed_as_kept():
    c = _null_chip()
    c.s["qubits"]["q1"]["xy"]["core"] = "x"
    c.el("q1", "extra", core="x", opx_output=c.port("mw_outputs", "con1", 1))
    p = _rec(c.s)
    assert p["kept"] == [{"element": "qubits.q1.extra", "core": "x",
                          "note": "keeps 'x'; no longer shares with qubits.q1.xy"}]
    assert not any(f["dot_path"].startswith("qubits.q1.extra") for f in p["fixes"])


def test_element_on_two_fems_is_never_touched():
    c = _null_chip()
    c.el("q9", "xy", opx_output=c.port("mw_outputs", "con1", 1))
    c.el("q9", "resonator", opx_output=c.port("mw_outputs", "con1", 2),
         opx_input=c.port("mw_inputs", "con1", 1))
    p = _rec(c.s)
    assert not any("q9" in f["dot_path"] for f in p["fixes"])
    assert not any("q9" in ch["element"] for ch in p["changes"])


def test_unreadable_port_is_named_never_dropped():
    c = _null_chip()
    c.el("q7", "xy", opx_output="#/ports/mw_outputs/con1/1/99/missing")
    c.el("q7", "resonator", opx_output=c.port("mw_outputs", "con1", 1),
         opx_input=c.port("mw_inputs", "con1", 1))
    p = _rec(c.s)
    assert any(u["element"] == "qubits.q7.xy" and u["field"] == "opx_output" for u in p["unresolved"])
    assert not any("q7" in f["dot_path"] for f in p["fixes"])


# --- gating and the one panel line -------------------------------------------------
def test_still_over_on_firm_terms_has_no_button():
    c = Chip()
    for i in range(10):
        c.el(f"q{i}", "xy", opx_output=c.port("mw_outputs", "con1", 1))
    p = _rec(c.s)
    assert p["button"] is False and p["fixes"] == [] and p["changes"] == []
    assert p["panel_line"] == ("Static count still 10 / 8 on con1 FEM 1 after the recommendation; "
                               "move elements off that FEM.")


def test_over_only_through_estimated_terms_keeps_the_button_and_says_estimated():
    c = _null_chip()
    for i in range(4):
        _input_only(c, f"p{i}")
    p = _rec(c.s)
    assert p["cores"]["after"] == 9.0 and p["cores"]["firm_after"] == 5.0
    assert p["button"] is True and p["cores"]["estimated"] is True and p["fixes"]


def test_route_overage_alone_has_no_button_and_the_route_line():
    c = Chip()
    c.mw_qubit("q1")
    c.digital("q1", "xy", "con1", 1, n=11)
    p = _rec(c.s)
    assert p["button"] is False and p["fixes"] == [] and p["panel_line"] == cl.PANEL_ROUTES


def test_no_lower_layout_line():
    c = Chip()
    for i in range(1, 6):
        c.mw_qubit(f"q{i}", core_xy=f"q{i}_core", core_rr=f"q{i}_core")
    for i in range(4):
        _input_only(c, f"p{i}")
    p = _rec(c.s)
    assert p["cores"]["before"] == p["cores"]["after"] == 9.0
    assert p["button"] is False and p["fixes"] == []
    assert p["panel_line"] == "No lower core layout to recommend on con1 FEM 1; move elements off that FEM."


def test_still_over_wins_over_the_route_line():
    c = Chip()
    for i in range(10):
        c.el(f"q{i}", "xy", opx_output=c.port("mw_outputs", "con1", 1))
    c.digital("q0", "xy", "con1", 1, n=11)
    assert _rec(c.s)["panel_line"].startswith("Static count still")


# --- one implementation: the wizard's own build ---------------------------------------
def test_recommendation_equals_a_fresh_wizard_build(tmp_path):
    from tests.test_core_layout_build import _build, _cores, _env, _five_qubit_spec
    python = _env()
    spec_on = _five_qubit_spec()
    spec_off = copy.deepcopy(spec_on)
    spec_off["populate"]["options"] = {"pin_cores": False}
    _, on = _build(python, spec_on, tmp_path / "on")
    _, off = _build(python, spec_off, tmp_path / "off")
    assert set(_cores(off).values()) <= {None}
    want = {p: v for p, v in _cores(on).items() if v}
    off = {**off, **json.loads((tmp_path / "off" / "wiring.json").read_text(encoding="utf-8"))}
    got, unresolved = cl.recommended_assignment(off)
    assert want and got == want and unresolved == []


def test_pin_rule_is_the_build_function_itself():
    from quam_state_manager.core.script_emitter import _run_build
    assert cl._pin_rule() is _run_build()._pin_cores


# --- the staged apply door --------------------------------------------------------------
@pytest.fixture
def chip(tmp_path):
    c = _null_chip()
    del c.s["qubits"]["q2"]["xy"]["core"]
    c.s["qubits"]["q2"]["xy"]["thread"] = "q2"
    live = tmp_path / "chip"
    live.mkdir()
    (live / "state.json").write_text(json.dumps(c.s), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps({"network": {"host": "127.0.0.1"}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    client = app.test_client()
    assert client.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    ctx = app.config["contexts"][app.config["active_context"]]
    return {"app": app, "client": client, "ctx": ctx, "store": ctx["store"], "live": live}


def _items(chip):
    return chip["client"].get("/diagnostics/core-findings").get_json()["findings"][0]["fixes"]


def test_one_press_one_group_and_one_undo_restores_every_leaf(chip):
    store, client = chip["store"], chip["client"]
    before = copy.deepcopy(store.merged["qubits"])
    live_before = (chip["live"] / "state.json").read_bytes()
    items = _items(chip)
    assert {"action": "create_value", "dot_path": "qubits.q2.xy.core", "value": "q2_core"} in items
    r = client.post("/diagnostics/apply-fix", json={"items": items})
    assert r.status_code == 200, r.get_json()
    assert len({e.group_id for e in store.change_log}) == 1
    assert store.merged["qubits"]["q2"]["xy"]["core"] == "q2_core"
    assert store.merged["qubits"]["q2"]["xy"]["thread"] is None
    assert store.merged["qubits"]["q4"]["resonator"]["core"] == "q4_core"
    assert (chip["live"] / "state.json").read_bytes() == live_before  # staged only
    after = client.get("/diagnostics/core-findings").get_json()["findings"]
    assert not [f for f in after if f["kind"] == "fem_budget"]  # 5 / 8 now: no warning
    u = client.post("/undo", headers={"Accept": "application/json"})
    assert u.status_code == 200
    assert store.merged["qubits"] == before and "core" not in store.merged["qubits"]["q2"]["xy"]


@pytest.mark.parametrize("bad", [
    {"action": "create_value", "dot_path": "qubits.q1.xy.core", "value": "q1_core"},     # key exists
    {"action": "create_value", "dot_path": "qubits.q2.xy.thread2", "value": "q2_core"},  # not .core
    {"action": "create_value", "dot_path": "qubits.q8.xy.core", "value": "q8_core"},     # no parent
    {"action": "create_value", "dot_path": "qubits.q2.xy.core", "value": "other_core"},  # not offered
    {"action": "set_value", "dot_path": "qubits.q2.xy.thread", "value": None},           # JSON null
])
def test_refused_items_stage_nothing(chip, bad):
    r = chip["client"].post("/diagnostics/apply-fix", json={"items": [bad]})
    assert r.status_code in (400, 409) and "unsupported fix action" not in (r.get_json().get("error") or "")
    assert chip["store"].change_log == []


def test_json_null_thread_is_a_409_not_a_match(chip):
    r = chip["client"].post("/diagnostics/apply-fix", json={"items": [
        {"action": "set_value", "dot_path": "qubits.q2.xy.thread", "value": None}]})
    assert r.status_code == 409


def test_stale_offer_is_refused(chip):
    items = _items(chip)
    chip["client"].post("/diagnostics/apply-fix", json={"items": items})
    r = chip["client"].post("/diagnostics/apply-fix", json={"items": items})
    assert r.status_code == 409


def test_archive_chip_has_no_button_and_the_route_refuses(chip):
    chip["ctx"]["origin"] = "archive"
    fs = chip["client"].get("/diagnostics/core-findings").get_json()["findings"]
    assert fs and all(f["fixes"] == [] and f["preview"] is None for f in fs)
    r = chip["client"].post("/diagnostics/apply-fix", json={"items": [
        {"action": "set_value", "dot_path": "qubits.q1.xy.core", "value": "q1_core"}]})
    assert r.status_code == 409 and r.get_json()["error_kind"] == "archive_read_only"
    html = chip["client"].get("/topology").get_data(as_text=True)
    assert '"preview": {' not in html and '"preview":{' not in html


def test_auto_push_note_route(chip, monkeypatch):
    from quam_state_manager.web import routes
    assert chip["client"].get("/diagnostics/auto-push-note").get_json() == {"ok": True, "note": ""}
    monkeypatch.setattr(routes, "_auto_push_note", lambda ctx=None: " Auto-Sync (push) is ARMED, so x")
    assert chip["client"].get("/diagnostics/auto-push-note").get_json()["note"] == "Auto-Sync (push) is ARMED, so x"


@pytest.mark.parametrize("bad", [
    {"action": "create_value", "dot_path": "qubits.q1.xy.core", "value": "q1_core"},
    {"action": "create_value", "dot_path": "qubits.q2.xy.thread2", "value": "q2_core"},
])
def test_create_guard_holds_even_for_an_offered_item(chip, monkeypatch, bad):
    from quam_state_manager.core import core_layout as live_cl
    monkeypatch.setattr(live_cl, "offered_fixes", lambda state: [bad])
    r = chip["client"].post("/diagnostics/apply-fix", json={"items": [bad]})
    assert r.status_code == 400 and r.get_json()["error"] == "fix may only create a missing core key"
    assert chip["store"].change_log == []
