"""Add / remove flow details (core/diagram_add.py, core/diagram_remove.py).

- a new qubit / pair starts active only as its template is, with a visible
  choice, and an absent or empty active list is never written;
- a freed LF output that still carries a previous line's filters / delay says
  so, sorts after every clean one, and is cleared by default when picked;
- a new pair's CZ phase corrections start at 0 and its copied amplitudes are
  named as not calibrated for it;
- "+ New pulse" opens on the qubit the Pulses page is filtered to;
- a removal lists the extras entries that still name the removed entity.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from make_synthetic_chip import make_chip  # noqa: E402
from quam_state_manager.core import diagram_add as da  # noqa: E402
from quam_state_manager.core import diagram_remove as dr  # noqa: E402
from quam_state_manager.core.regen_merge import _merged_doc  # noqa: E402


@pytest.fixture(autouse=True)
def _env_check_passes(monkeypatch):
    from quam_state_manager.web import diagram_remove_routes as DR
    monkeypatch.setattr(DR, "env_python", lambda: "python")
    monkeypatch.setattr(DR, "run_env_check", lambda py, st, wi: {
        "ok": True, "error": None, "config": {"elements": {}}, "seconds": 0.0})
    DR._VERDICTS.clear()


def _chip(n=20, edit=None):
    s, w = make_chip(n)
    if edit:
        edit(s, w)
    return s, w, _merged_doc(s, w)


def _qubit_plan(m, **kw):
    d = da.qubit_defaults(m)
    inv = da.inventory(m)
    dc = da.drive_candidates(m, inv, d["drive_rf"], prefer_fem=d["fem"], if_hz=d["if_hz"])
    fc = da.flux_candidates(inv)
    args = dict(name=d["name"], feedline=d["feedline"],
                drive=(dc[0]["con"], dc[0]["fem"], dc[0]["port"]),
                flux=(fc[0]["con"], fc[0]["fem"], fc[0]["port"]),
                drive_rf=d["drive_rf"], readout_rf=d["readout_rf"], inv=inv)
    args.update(kw)
    return d, da.plan_qubit(m, **args)


def _sets(plan):
    return dict(plan["sets"])


# ---------------------------------------------------------- active lists

def _template_off(s, _w):
    # the first feedline's template is its last plain member (q10)
    s["active_qubit_names"] = [q for q in s["active_qubit_names"] if q != "q10"]


class TestActive:
    def test_an_inactive_template_gives_an_inactive_qubit_by_default(self):
        _s, _w, m = _chip(edit=_template_off)
        d, p = _qubit_plan(m)
        assert d["template"] == "q10" and d["active"]["default"] is False
        assert "active_qubit_names" not in _sets(p)
        assert p["summary"]["active"]["written"] is False

    def test_checking_the_box_adds_it(self):
        _s, _w, m = _chip(edit=_template_off)
        d, p = _qubit_plan(m, active=True)
        assert _sets(p)["active_qubit_names"][-1] == d["name"]

    def test_an_active_template_still_gives_an_active_qubit(self):
        _s, _w, m = _chip()
        d, p = _qubit_plan(m)
        assert _sets(p)["active_qubit_names"][-1] == d["name"]
        _d, p = _qubit_plan(m, active=False)
        assert "active_qubit_names" not in _sets(p)

    @pytest.mark.parametrize("mode", ["absent", "empty"])
    def test_an_absent_or_empty_pair_list_is_never_written(self, mode):
        def edit(s, _w):
            if mode == "absent":
                del s["active_qubit_pair_names"]
            else:
                s["active_qubit_pair_names"] = []
        _s, _w, m = _chip(edit=edit)
        for active in (None, True):
            p = da.plan_pair(m, "q20", "q11", active=active)
            assert not p.get("error"), p
            assert "active_qubit_pair_names" not in _sets(p)
            assert p["summary"]["active"]["mode"] == mode
        if mode == "absent":
            assert "active_qubit_pair_names" not in m

    def test_an_inactive_template_pair_gives_an_inactive_pair(self):
        def edit(s, _w):
            s["active_qubit_pair_names"] = ["q1-q2"]
        _s, _w, m = _chip(edit=edit)
        p = da.plan_pair(m, "q20", "q11")
        assert p["summary"]["template"] != "q1-q2"
        assert "active_qubit_pair_names" not in _sets(p)


# ----------------------------------------------------- freed LF outputs

FIR = [0.01] * 48
IIR = [[0.1, 200.0], [0.05, 1000.0], [0.02, 5000.0]]


def _freed_port(s, _w):
    """Declare the lowest free LF output as one a removed line left behind,
    carrying its FIR, IIR terms and delay."""
    ao = s["ports"]["analog_outputs"]
    con = sorted(ao)[-1]
    fem = sorted(ao[con], key=int)[-1]
    used = {int(p) for p in ao[con][fem]}
    pid = min(set(range(1, 9)) - used)
    port = copy.deepcopy(next(iter(ao[con][fem].values())))
    port.update(port_id=pid, feedforward_filter=FIR, exponential_filter=IIR, delay=62)
    ao[con][fem][str(pid)] = port
    s["_freed"] = (con, int(fem), pid)


class TestFreedPort:
    def test_the_freed_port_names_what_it_carries_and_is_not_the_default(self):
        s, _w, m = _chip(edit=_freed_port)
        con, fem, pid = s.pop("_freed")
        cands = da.flux_candidates(da.inventory(m), limit=0)
        freed = next(c for c in cands if (c["con"], c["fem"], c["port"]) == (con, fem, pid))
        assert freed["carries"] == ["FIR 48 taps", "3 exponential terms", "delay 62 ns"]
        assert "FIR 48 taps, 3 exponential terms, delay 62 ns" in freed["why"]
        assert not cands[0]["carries"]
        assert all(not c["carries"] for c in cands[:cands.index(freed)])

    def test_picking_it_clears_its_filters_by_default_and_keep_keeps_them(self):
        s, _w, m = _chip(edit=_freed_port)
        con, fem, pid = s.pop("_freed")
        base = f"ports.analog_outputs.{con}.{fem}.{pid}"
        _d, p = _qubit_plan(m, flux=(con, fem, pid))
        assert not p.get("error"), p
        sets = _sets(p)
        assert sets[f"{base}.feedforward_filter"] is None
        assert sets[f"{base}.exponential_filter"] is None
        # the delay aligns the line (crosstalk latency included): never cleared
        assert f"{base}.delay" not in sets
        fc = p["summary"]["flux_carry"]
        assert fc["choice"] == "clear" and fc["delay_kept"] == 62
        assert fc["filters"] == ["FIR 48 taps", "3 exponential terms"]
        _d, p = _qubit_plan(m, flux=(con, fem, pid), port_filters="keep")
        assert not any(k.startswith(base) for k in _sets(p))
        assert p["summary"]["flux_carry"]["choice"] == "keep"

    def test_a_clean_free_port_is_chosen_exactly_as_before(self):
        _s, _w, m = _chip()
        inv = da.inventory(m)
        cands = da.flux_candidates(inv, limit=0)
        old = sorted(cands, key=lambda c: (0 if not c["new"] else 1,
                                           c["con"], c["fem"], c["port"]))
        assert [c["label"] for c in cands] == [c["label"] for c in old]
        assert all(not c["carries"] and c["why"] in ("declared", "new port") for c in cands)
        _d, p = _qubit_plan(m)
        assert p["summary"]["flux_carry"] is None
        assert all(not k.startswith("ports.") for k in _sets(p))


# ------------------------------------------------------------- pair copy

class TestPairCopy:
    def test_phase_corrections_start_at_zero_and_amplitudes_are_named(self):
        _s, _w, m = _chip(100)
        p = da.plan_pair(m, "q100", "q91")
        assert not p.get("error"), p
        macro = dict(p["creates"])["qubit_pairs.q100-q91"]["macros"]["cz_flattop"]
        assert macro["phase_shift_control"] == 0.0 and macro["phase_shift_target"] == 0.0
        s = p["summary"]
        assert {c["value"] for c in s["phase_cleared"]} == {0.1, 0.2}
        assert {"path": "qubit_pairs.q100-q91.macros.cz_flattop.flux_pulse_qubit.amplitude",
                "value": 0.2} in s["copied_amplitudes"]
        # the template's own macro is untouched
        assert m["qubit_pairs"][s["template"]]["macros"]["cz_flattop"]["phase_shift_control"] == 0.1


# ----------------------------------------------------------- removal

def test_a_removal_lists_extras_that_name_it():
    def edit(s, _w):
        s["qubit_pairs"]["q1-q2"]["extras"] = {"q1-q2-q5": 1, "q50_note": 2,
                                               "note": "retune after q5 moves"}
        s["extras"] = {"chip_name": "test", "q15": "unrelated"}
    _s, _w, m = _chip(edit=edit)
    p = dr.plan_remove_qubit(m, "q5")
    assert not p.get("error"), p
    got = {(x["path"], x["where"]) for x in p["summary"]["extras_mention"]}
    assert got == {("qubit_pairs.q1-q2.extras.q1-q2-q5", "key"),
                   ("qubit_pairs.q1-q2.extras.note", "value")}
    # never deleted: extras stay as written
    assert not any("extras" in d for d in p["deletes"])


# ------------------------------------------------------------- routes

@pytest.fixture
def app_env(tmp_path, request):
    from quam_state_manager.web.app import create_app
    edit = getattr(request, "param", None)
    s, w = make_chip(20)
    if edit:
        edit(s, w)
    s.pop("_freed", None)
    chip = tmp_path / "chip"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps(s), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps(w), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(chip)})
    ctx = next(iter(app.config["contexts"].values()))
    return {"c": c, "store": ctx["store"]}


def _token(c):
    from quam_state_manager.web import routes as R
    with c.application.test_request_context():
        return R._active_chip_token()


def _form(c, store, **extra):
    d = da.qubit_defaults(store.merged)
    j = c.get(f"/api/diagram-add/ports?feedline={d['feedline']}&drive_rf={d['drive_rf']}").get_json()
    dr_, fl = j["drive"][0], j["flux"][0]
    f = {"kind": "qubit", "name": d["name"], "feedline": d["feedline"],
         "drive_rf": str(d["drive_rf"]), "readout_rf": str(d["readout_rf"]),
         "drive": f"{dr_['con']}/{dr_['fem']}/{dr_['port']}",
         "flux": f"{fl['con']}/{fl['fem']}/{fl['port']}", "expect_chip": _token(c)}
    f.update(extra)
    return d, f


@pytest.mark.parametrize("app_env", [_template_off], indirect=True)
def test_the_panel_shows_the_active_box_and_the_stage_follows_it(app_env):
    c, st = app_env["c"], app_env["store"]
    html = c.get("/diagram-add/panel?kind=qubit").get_data(as_text=True)
    assert "data-cm-add-active-row" in html and "not active" in html
    assert 'name="active" value="0"' in html
    box = html.split("data-cm-add-active")[1].split(">")[0]
    assert "checked" not in box
    d, form = _form(c, st, active="0")
    rev = c.post("/diagram-add/stage", data=dict(form, dry="1")).get_data(as_text=True)
    assert "does not join active_qubit_names" in rev
    r = c.post("/diagram-add/stage", data=dict(form, dry="0"))
    assert r.status_code == 200, r.data
    assert d["name"] in st.merged["qubits"] and d["name"] not in st.merged["active_qubit_names"]
    assert "did not join active_qubit_names" in r.get_data(as_text=True)
    # the box checked against the template's default: the stage follows the box
    c.post("/undo")
    d, form = _form(c, st)
    form["active"] = ["0", "1"]
    r = c.post("/diagram-add/stage", data=dict(form, dry="0"))
    assert r.status_code == 200, r.data
    assert st.merged["active_qubit_names"][-1] == d["name"]
    assert "joined active_qubit_names" in r.get_data(as_text=True)


@pytest.mark.parametrize("app_env", [_freed_port], indirect=True)
def test_the_review_and_the_result_line_state_the_filter_choice(app_env):
    c, st = app_env["c"], app_env["store"]
    html = c.get("/diagram-add/panel?kind=qubit").get_data(as_text=True)
    assert "data-cm-add-filters-row" in html and "FIR 48 taps" in html
    inv = da.inventory(st.merged)
    freed = next(x for x in da.flux_candidates(inv, limit=0) if x["carries"])
    _d, form = _form(c, st, flux=f"{freed['con']}/{freed['fem']}/{freed['port']}")
    rev = c.post("/diagram-add/stage", data=dict(form, dry="1")).get_data(as_text=True)
    assert "port settings cleared" in rev and "data-cm-add-carry-note" in rev
    r = c.post("/diagram-add/stage", data=dict(form, dry="0"))
    assert r.status_code == 200, r.data
    port = st.merged["ports"]["analog_outputs"][freed["con"]][str(freed["fem"])][str(freed["port"])]
    assert port["feedforward_filter"] is None and port["exponential_filter"] is None
    assert port["delay"] == 62
    out = r.get_data(as_text=True)
    assert "filters (FIR 48 taps, 3 exponential terms) were cleared" in out
    want = "delay 62 ns kept — recalibrate this line's timing (z-line delay) for the new element"
    assert want in out.replace("&#39;", "'")
    assert "data-cm-add-delay-kept" in rev and "delay 62 ns kept &mdash; recalibrate" in rev


def test_the_pair_review_names_the_copied_amplitudes(app_env):
    c = app_env["c"]
    r = c.post("/diagram-add/stage", data={"kind": "pair", "control": "q20", "target": "q11",
                                           "dry": "1", "expect_chip": _token(c)})
    html = r.get_data(as_text=True)
    assert r.status_code == 200, html
    assert "data-cm-add-copied-amps" in html and "not calibrated for this pair" in html
    assert "phase corrections start at 0" in html


def test_new_pulse_opens_on_the_filtered_owner(app_env):
    c = app_env["c"]
    page = c.get("/pulses").get_data(as_text=True)
    btn = page.split('class="btn-sm pulse-new-btn"')[1].split(">")[0]
    assert 'hx-include="#pulses-owner-pick"' in btn
    html = c.get("/pulse/new?owner=q7").get_data(as_text=True)
    assert "<option selected>q7</option>" in html
    html = c.get("/pulse/new?owner=q7&qubit=q3").get_data(as_text=True)
    assert "<option selected>q3</option>" in html


def test_the_remove_panel_lists_extras_that_name_it(app_env):
    c, st = app_env["c"], app_env["store"]
    st.merged["qubit_pairs"]["q1-q2"]["extras"] = {"q1-q2-q5": 1}
    html = c.get("/diagram-remove/panel?kind=qubit&id=q5").get_data(as_text=True)
    assert "data-cm-rm-mention" in html and "qubit_pairs.q1-q2.extras.q1-q2-q5" in html


def _template_plays_a_missing_name(s, _w):
    """Every pair gate slot plays, by name, an operation no member has."""
    for p in s["qubit_pairs"].values():
        for mac in (p.get("macros") or {}).values():
            if isinstance(mac, dict) and isinstance(mac.get("flux_pulse_qubit"), dict):
                mac["flux_pulse_qubit"]["id"] = "cz_gone_pulse"


class TestPairUnplayable:
    def test_a_clean_template_gives_no_line(self):
        _s, _w, m = _chip()
        p = da.plan_pair(m, "q20", "q11")
        assert not p.get("error"), p
        assert p["summary"]["unplayable"] == []

    def test_the_plan_s_own_refusal_is_named_for_the_new_pair(self):
        _s, _w, m = _chip(edit=_template_plays_a_missing_name)
        p = da.plan_pair(m, "q20", "q11")
        assert not p.get("error"), p
        u = p["summary"]["unplayable"]
        assert u and all("not playable" in x and "cz_gone_pulse" in x for x in u), u
        assert m["qubit_pairs"].get(p["summary"]["name"]) is None  # never written


@pytest.mark.parametrize("app_env", [_template_plays_a_missing_name], indirect=True)
def test_the_pair_review_says_a_copied_gate_is_not_playable(app_env):
    c = app_env["c"]
    r = c.post("/diagram-add/stage", data={"kind": "pair", "control": "q20", "target": "q11",
                                           "dry": "1", "expect_chip": _token(c)})
    html = r.get_data(as_text=True)
    assert "data-cm-add-unplayable" in html and "not playable" in html
