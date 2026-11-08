"""Add a qubit or a pair from the chip diagram (core/diagram_add.py + routes).

The load-bearing property: an add writes only NEW subtrees (plus the new
name appended to the chip's active-name lists), so every value that existed
before is unchanged, resolved or raw. Checked on a synthetic 100-qubit chip
(tests/make_synthetic_chip.py) through the real routes, and the stage is one
change group that one /undo removes.
"""

from __future__ import annotations

import json
import time

import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from make_synthetic_chip import make_chip, write_chip  # noqa: E402
from quam_state_manager.core import diagram_add as da  # noqa: E402
from quam_state_manager.core.regen_merge import _merged_doc, _resolved_leaves  # noqa: E402


@pytest.fixture(autouse=True)
def _env_check_passes(monkeypatch):
    """Staging needs a passing env check (Quam.load + generate_config of the
    result); these tests are about the plan and the staging, so the check
    answers "loads" without spawning an env. Its own pins are in
    tests/test_diagram_remove.py."""
    from quam_state_manager.web import diagram_remove_routes as DR
    monkeypatch.setattr(DR, "env_python", lambda: "python")
    monkeypatch.setattr(DR, "run_env_check", lambda py, st, wi: {
        "ok": True, "error": None, "config": {"elements": {}}, "seconds": 0.0})
    DR._VERDICTS.clear()


def _doc(n=100):
    s, w = make_chip(n)
    return _merged_doc(s, w), s, w


def _raw(node, path="", out=None):
    out = {} if out is None else out
    if isinstance(node, dict):
        for k, v in node.items():
            _raw(v, f"{path}.{k}", out)
    else:
        out[path] = json.dumps(node, sort_keys=True)
    return out


# ------------------------------------------------------------------ pure

class TestNaming:
    def test_follows_the_feedline_prefix(self):
        ids = ["qW1", "qW2", "qX1", "qX2", "qX5"]
        assert da.next_qubit_name(ids, ["qX1", "qX2", "qX5"]) == "qX6"

    def test_plain_numbering(self):
        assert da.next_qubit_name([f"q{i}" for i in range(1, 101)], ["q91", "q100"]) == "q101"

    def test_never_a_taken_name(self):
        assert da.next_qubit_name(["q1", "q2", "q3"], ["q1"]) == "q4"

    def test_pair_name_full_form_by_default(self):
        m, _s, _w = _doc(20)
        assert da.pair_name(m, "q21", "q20") == "q21-q20"


class TestPorts:
    def test_band_rule_refuses_a_band_the_peer_forbids(self):
        m, s, _w = _doc(20)
        inv = da.inventory(m)
        # out 2's peer is out 3; make out 3 band 2 and ask for a 9 GHz drive:
        # the only band holding a 8.9 GHz LO is 3, and 2 + 3 is not legal
        con, fem = "con1", 1
        for pid in (2, 3):
            inv["used"].discard(("mw_outputs", con, fem, pid))
        inv["ports"]["mw_outputs"][con][str(fem)].pop("2", None)
        inv["ports"]["mw_outputs"][con][str(fem)]["3"]["band"] = 2
        c = da.drive_candidates(m, inv, 9.0e9, limit=10 ** 6)
        assert ("con1", 1, 2) not in {(x["con"], x["fem"], x["port"]) for x in c}

    def test_a_declared_port_keeps_its_lo_and_the_if_window(self):
        m, s, _w = _doc(20)
        inv = da.inventory(m)
        # free a declared drive output: its LO is f01 - 100 MHz
        inv["used"].discard(("mw_outputs", "con1", 1, 2))
        lo = inv["ports"]["mw_outputs"]["con1"]["1"]["2"]["upconverter_frequency"]
        near = {(x["con"], x["fem"], x["port"]) for x in
                da.drive_candidates(m, inv, lo + 300e6, limit=10 ** 6)}
        far = {(x["con"], x["fem"], x["port"]) for x in
               da.drive_candidates(m, inv, lo + 450e6, limit=10 ** 6)}
        assert ("con1", 1, 2) in near and ("con1", 1, 2) not in far

    def test_only_mw_fem_outputs_drive_and_only_lf_fem_outputs_flux(self):
        m, _s, _w = _doc(100)
        inv = da.inventory(m)
        for c in da.drive_candidates(m, inv, 5.1e9, limit=10 ** 6):
            assert inv["fems"][(c["con"], c["fem"])] == "mw"
            assert ("mw_outputs", c["con"], c["fem"], c["port"]) not in inv["used"]
        for c in da.flux_candidates(inv, limit=10 ** 6):
            assert inv["fems"][(c["con"], c["fem"])] == "lf"
            assert ("analog_outputs", c["con"], c["fem"], c["port"]) not in inv["used"]


class TestQubitPlan:
    def _plan(self, m, **kw):
        d = da.qubit_defaults(m, kw.pop("feedline", None))
        inv = da.inventory(m)
        dc = da.drive_candidates(m, inv, d["drive_rf"], prefer_fem=d["fem"], if_hz=d["if_hz"])
        fc = da.flux_candidates(inv)
        args = dict(name=d["name"], feedline=d["feedline"],
                    drive=(dc[0]["con"], dc[0]["fem"], dc[0]["port"]),
                    flux=(fc[0]["con"], fc[0]["fem"], fc[0]["port"]),
                    drive_rf=d["drive_rf"], readout_rf=d["readout_rf"], inv=inv)
        args.update(kw)
        return d, da.plan_qubit(m, **args)

    def test_every_write_is_a_create_of_a_path_that_does_not_exist(self):
        m, _s, _w = _doc(100)
        _d, p = self._plan(m)
        assert not p.get("error"), p
        for path, _v in p["creates"]:
            node = m
            keys = path.split(".")
            for k in keys[:-1]:
                node = node[k]
            assert keys[-1] not in node, path
        assert [s[0] for s in p["sets"]] == ["active_qubit_names"]

    def test_the_copy_is_the_new_qubit_and_not_the_template(self):
        m, _s, _w = _doc(100)
        d, p = self._plan(m, drive_rf=5.05e9)
        q = dict(p["creates"])[f"qubits.{d['name']}"]
        assert q["id"] == d["name"] and q["xy"]["thread"] == d["name"]
        assert q["f_01"] == 5.05e9 and q["xy"]["RF_frequency"] == 5.05e9
        assert q["T1"] is None and q["resonator"]["confusion_matrix"] is None
        assert q["xy"]["opx_output"] == f"#/wiring/qubits/{d['name']}/xy/opx_output"
        assert q["z"]["joint_offset"] == 0
        txt = json.dumps(q)
        assert f"/{d['template']}/" not in txt and "qubit_pairs" not in txt

    def test_a_used_port_is_refused(self):
        m, _s, _w = _doc(20)
        _d, p = self._plan(m, drive=("con1", 1, 2))
        assert "not a free MW-FEM output" in p["error"]

    def test_a_readout_outside_the_feedline_if_window_is_refused(self):
        m, _s, _w = _doc(20)
        d = da.qubit_defaults(m)
        _d, p = self._plan(m, readout_rf=d["readout_rf"] + 1e9)
        assert "IF window" in p["error"]


class TestPairPlan:
    def test_member_ops_follow_the_new_pair(self):
        m, _s, _w = _doc(100)
        p = da.plan_pair(m, "q100", "q91")
        assert not p.get("error"), p
        cr = dict(p["creates"])
        pair = cr["qubit_pairs.q100-q91"]
        assert pair["qubit_control"] == "#/qubits/q100" and pair["qubit_target"] == "#/qubits/q91"
        assert pair["macros"]["cz_flattop"]["fidelity"] == {}
        op = cr["qubits.q100.z.operations.cz_flattop_pulse_q91"]
        assert op["length"].startswith("#/qubit_pairs/q100-q91/")

    def test_an_existing_pair_is_refused(self):
        m, _s, _w = _doc(20)
        assert "already a pair" in da.plan_pair(m, "q2", "q1")["error"]


# ---------------------------------------------------------------- routes

@pytest.fixture
def env(tmp_path):
    from quam_state_manager.web.app import create_app
    chip = write_chip(tmp_path / "chip", 100)
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(chip)})
    ctx = next(iter(app.config["contexts"].values()))
    return {"app": app, "c": c, "store": ctx["store"], "chip": chip}


def _form(c, store):
    d = da.qubit_defaults(store.merged)
    j = c.get(f"/api/diagram-add/ports?feedline={d['feedline']}&drive_rf={d['drive_rf']}").get_json()
    dr, fl = j["drive"][0], j["flux"][0]
    return d, {"kind": "qubit", "name": d["name"], "feedline": d["feedline"],
               "drive_rf": str(d["drive_rf"]), "readout_rf": str(d["readout_rf"]),
               "drive": f"{dr['con']}/{dr['fem']}/{dr['port']}",
               "flux": f"{fl['con']}/{fl['fem']}/{fl['port']}",
               "expect_chip": _token(c)}


def _token(c):
    from quam_state_manager.web import routes as R
    with c.application.test_request_context():
        return R._active_chip_token()


class TestRoutes:
    def test_the_qubits_page_carries_the_door(self, env):
        html = env["c"].get("/qubits").get_data(as_text=True)
        assert 'data-cm-add="1"' in html and 'id="cm-add-panel"' in html

    def test_panel_opens_with_the_next_name(self, env):
        html = env["c"].get("/diagram-add/panel?kind=qubit").get_data(as_text=True)
        assert 'value="q101"' in html and "free MW-FEM outputs" in html

    def test_review_shows_only_the_change_and_writes_nothing(self, env):
        st = env["store"]
        seq = st.mutation_seq
        _d, form = _form(env["c"], st)
        r = env["c"].post("/diagram-add/stage", data=dict(form, dry="1"))
        assert r.status_code == 200, r.data
        html = r.get_data(as_text=True)
        assert "No existing value changes" in html and "qubits.q101" in html
        assert "qubits.q100." not in html
        assert st.mutation_seq == seq and not st.change_log

    def test_stage_leaves_every_existing_value_unchanged_and_undoes_in_one_press(self, env):
        st = env["store"]
        before_res = {t: _resolved_leaves(st.merged, t, cycles_as_leaves=True)
                      for t in ("qubits", "qubit_pairs", "ports", "wiring")}
        before_raw = _raw(json.loads(json.dumps(st.merged)))
        _d, form = _form(env["c"], st)
        r = env["c"].post("/diagram-add/stage", data=dict(form, dry="0"))
        assert r.status_code == 200, r.data
        assert "q101" in st.merged["qubits"]
        assert len({e.group_id for e in st.change_log}) == 1
        after_raw = _raw(st.merged)
        diff = [k for k, v in before_raw.items() if after_raw.get(k) != v]
        assert diff == [".active_qubit_names"], diff
        for t, lv in before_res.items():
            now = _resolved_leaves(st.merged, t, cycles_as_leaves=True)
            assert all(now.get(k) == v for k, v in lv.items()), t
        # a pair onto the new qubit, then one Ctrl+Z each
        r = env["c"].post("/diagram-add/stage", data={"kind": "pair", "control": "q101",
                                                       "target": "q100", "dry": "0",
                                                       "expect_chip": _token(env['c'])})
        assert r.status_code == 200, r.data
        assert "q101-q100" in st.merged["qubit_pairs"]
        env["c"].post("/undo")
        assert "q101-q100" not in st.merged["qubit_pairs"] and "q101" in st.merged["qubits"]
        env["c"].post("/undo")
        assert "q101" not in st.merged["qubits"]
        assert _raw(st.merged) == before_raw

    def test_the_live_files_are_untouched_by_a_stage(self, env):
        live = (env["chip"] / "state.json").read_bytes()
        _d, form = _form(env["c"], env["store"])
        env["c"].post("/diagram-add/stage", data=dict(form, dry="0"))
        assert (env["chip"] / "state.json").read_bytes() == live

    def test_a_stale_chip_token_is_refused(self, env):
        _d, form = _form(env["c"], env["store"])
        r = env["c"].post("/diagram-add/stage", data=dict(form, dry="0", expect_chip="nope"))
        assert r.status_code == 409 and "q101" not in env["store"].merged["qubits"]

    def test_scale_100_qubits(self, env):
        c = env["c"]
        t = time.perf_counter()
        c.get("/diagram-add/panel?kind=qubit")
        c.get("/api/diagram-add/ports?feedline=con2/6/1&drive_rf=5.2e9")
        assert time.perf_counter() - t < 2.0


class TestAutoSyncNote:
    """With Auto-Sync pushing, a stage reaches the chip at once; the panel says so."""

    def _ctx(self, env):
        return next(iter(env["app"].config["contexts"].values()))

    @pytest.mark.parametrize("kind", ["qubit", "pair&a=q1&b=q2"])
    def test_the_note_shows_only_while_auto_sync_pushes(self, env, kind):
        from quam_state_manager.web import routes as R
        ctx = self._ctx(env)
        ctx.pop("auto_apply", None)
        html = env["c"].get(f"/diagram-add/panel?kind={kind}").get_data(as_text=True)
        assert "data-cm-add-autosync" not in html
        ctx["auto_apply"] = R._new_auto_session(pull=True, pull_replace=False, push=False)
        html = env["c"].get(f"/diagram-add/panel?kind={kind}").get_data(as_text=True)
        assert "data-cm-add-autosync" not in html
        ctx["auto_apply"] = R._new_auto_session(pull=True, pull_replace=False, push=True)
        html = env["c"].get(f"/diagram-add/panel?kind={kind}").get_data(as_text=True)
        assert "data-cm-add-autosync" in html and "writes it to the chip at once" in html


@pytest.fixture
def coupler_env(tmp_path):
    from quam_state_manager.web.app import create_app
    chip = write_chip(tmp_path / "coupled", 100, couplers=True)
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    c = app.test_client()
    c.post("/load", data={"folder": str(chip)})
    ctx = next(iter(app.config["contexts"].values()))
    return {"c": c, "store": ctx["store"], "chip": chip}


def _coupler_form(c, st):
    port = next(x for x in da.flux_candidates(da.inventory(st.merged)) if x["new"])
    return {"kind": "pair", "control": "q100", "target": "q91",
            "flux": f"{port['con']}/{port['fem']}/{port['port']}",
            "expect_chip": _token(c)}


class TestCouplerPair:
    def test_structure_and_cleared_values(self, coupler_env):
        st, c = coupler_env["store"], coupler_env["c"]
        assert len(st.merged["qubits"]) == 100 and len(st.merged["qubit_pairs"]) == 180
        form = _coupler_form(c, st)
        r = c.post("/diagram-add/stage", data=dict(form, dry="0"))
        assert r.status_code == 200, r.data
        pair = st.merged["qubit_pairs"]["q100-q91"]
        assert pair["__class__"].endswith(".FluxTunableTransmonPair")
        assert (pair["id"], pair["qubit_control"], pair["qubit_target"]) == (
            "q100-q91", "#/qubits/q100", "#/qubits/q91")
        cp = pair["coupler"]
        assert cp["id"] == "coupler_q100_q91"
        assert cp["opx_output"] == "#/wiring/qubit_pairs/q100-q91/c/opx_output"
        assert all(cp[k] == 0 for k in ("decouple_offset", "interaction_offset",
                                        "arbitrary_offset", "opx_output_offset"))
        assert pair["macros"]["cz"] == "#./cz_coupler"
        macro = pair["macros"]["cz_coupler"]
        assert macro["fidelity"] == {}
        assert macro["coupler_flux_pulse"] == (
            "#/qubit_pairs/q100-q91/coupler/operations/cz_coupler_pulse_q100_q91")
        assert da._resolve(st.merged, macro["coupler_flux_pulse"])["amplitude"] == 0.12
        w = st.merged["wiring"]["qubit_pairs"]["q100-q91"]["c"]
        assert w == {"control_qubit": "#/qubits/q100", "target_qubit": "#/qubits/q91",
                     "opx_output": "#/ports/analog_outputs/" + form["flux"]}
        assert da._resolve(st.merged, cp["opx_output"])["__class__"].endswith("LFFEMAnalogOutputPort")

    def test_panel_review_inventory_and_undo(self, coupler_env, monkeypatch):
        st, c = coupler_env["store"], coupler_env["c"]
        before = json.loads(json.dumps(st.merged))
        leaves = {t: _resolved_leaves(st.merged, t, cycles_as_leaves=True)
                  for t in ("qubits", "qubit_pairs", "wiring", "ports")}
        from quam_state_manager.web import diagram_add_routes as routes
        routes._INV_MEMO.clear()
        calls = []
        inventory = da.inventory
        def counted(m):
            calls.append(1)
            return inventory(m)
        monkeypatch.setattr(da, "inventory", counted)
        html = c.get("/diagram-add/panel?kind=pair&a=q100&b=q91").get_data(as_text=True)
        assert 'name="flux"' in html and 'aria-label="Coupler output"' in html
        assert 'value="q100"' in html and 'data-cm-add-swap' in html
        form = _coupler_form(c, st)
        calls.clear()
        seq = st.mutation_seq
        r = c.post("/diagram-add/stage", data=dict(form, dry="1"))
        assert r.status_code == 200, r.data
        html = r.get_data(as_text=True)
        assert "qubit_pairs.q100-q91.coupler" in html and "No existing value changes" in html
        assert "qubit_pairs.q90-q100.coupler" not in html
        assert st.mutation_seq == seq and st.merged == before and not st.change_log
        con, fem, port = form["flux"].split("/")
        plan = da.plan_pair(st.merged, "q100", "q91", flux=(con, int(fem), int(port)),
                            inv=routes._inv(st))
        assert [r["value"] for r in da.review_rows(plan) if r["op"] == "append"] == [["q100-q91"]]
        r = c.post("/diagram-add/stage", data=dict(form, dry="0"))
        assert r.status_code == 200, r.data
        assert calls == []
        assert st.merged["active_qubit_pair_names"] == before["active_qubit_pair_names"] + ["q100-q91"]
        assert len({e.group_id for e in st.change_log}) == 1
        for top, old in leaves.items():
            now = _resolved_leaves(st.merged, top, cycles_as_leaves=True)
            assert all(now.get(k) == v for k, v in old.items()), top
        live = json.loads((coupler_env["chip"] / "state.json").read_text())
        assert "q100-q91" not in live["qubit_pairs"]
        c.get("/diagram-add/panel?kind=pair&a=q100&b=q92")
        assert calls == [1]
        c.post("/undo")
        assert st.merged == before and not st.change_log

    @pytest.mark.parametrize("port", ["con1/1/1", "taken", ""])
    def test_bad_output_refused(self, coupler_env, port):
        st, c = coupler_env["store"], coupler_env["c"]
        before = json.loads(json.dumps(st.merged))
        form = _coupler_form(c, st)
        if port == "taken":
            port = next(iter(st.merged["wiring"]["qubit_pairs"].values()))["c"]["opx_output"].removeprefix("#/ports/analog_outputs/")
        r = c.post("/diagram-add/stage", data=dict(form, flux=port, dry="0"))
        assert r.status_code == 400 and "LF-FEM" in r.get_data(as_text=True)
        assert st.merged == before and not st.change_log

    @pytest.mark.parametrize("bias_tee", [False, True])
    def test_bias_members_refused_in_panel(self, coupler_env, bias_tee):
        st, c = coupler_env["store"], coupler_env["c"]
        q = st.merged["qubits"]["q100"]
        bias = {"__class__": "example.QdacBiasLine", "channel": 1}
        if bias_tee:
            q["bias"] = bias
        else:
            q["z"] = bias
        html = c.get("/diagram-add/panel?kind=pair&a=q100&b=q91").get_data(as_text=True)
        assert "QDAC or bias-tee" in html and 'type="submit"' not in html
        assert c.post("/diagram-add/stage", data=dict(_coupler_form(c, st), dry="0")).status_code == 400

    def test_port_settings_without_calibration_and_nearest_sibling(self):
        s, w = make_chip(100, couplers=True)
        m = _merged_doc(s, w)
        inv = da.inventory(m)
        pick = next(c for c in da.flux_candidates(inv) if c["new"])
        con, fem, pid = pick["con"], pick["fem"], pick["port"]
        sibling = next(iter(inv["ports"]["analog_outputs"][con][str(fem)].values()))
        sibling.update({"offset": 0.4, "delay": 32, "feedforward_filter": [0.1],
                        "feedback_filter": [0.2], "filter_fir_taps": [0.3],
                        "exponential_dc_gain": 0.98,
                        "crosstalk": {"1": 0.5}, "output_mode": "amplified",
                        "sampling_rate": 2e9, "shareable": True})
        plan = da.plan_pair(m, "q100", "q91", flux=(con, fem, pid), inv=inv)
        assert not plan.get("error"), plan
        port = dict(plan["creates"])[f"ports.analog_outputs.{con}.{fem}.{pid}"]
        assert (port["controller_id"], port["fem_id"], port["port_id"]) == (con, fem, pid)
        assert port["output_mode"] == "amplified" and port["sampling_rate"] == 2e9
        assert port["__class__"] == sibling["__class__"]
        assert port["offset"] is None and port["delay"] == 0 and port["shareable"] is False
        assert port["crosstalk"] == {} and all(port[k] is None for k in (
            "feedforward_filter", "feedback_filter", "filter_fir_taps", "exponential_dc_gain"))


    def test_declared_port_and_template_wiring_spelling(self):
        s, w = make_chip(100, couplers=True)
        m = _merged_doc(s, w)
        tpid = da.pick_pair_template(m, "q100", "q91")
        pair = m["qubit_pairs"][tpid]
        wire = m["wiring"]["qubit_pairs"][tpid]
        tc, _tt = da._pair_membership(pair, m)
        wire[f"flux_{tc}"] = wire.pop("c")
        pair["coupler"]["opx_output"] = f"#/wiring/qubit_pairs/{tpid}/flux_{tc}/opx_output"
        pair["extras"] = {"measured": 1}
        pair["confusion"] = [[0.9, 0.1]]
        pair["gef_result"] = [1, 2]
        pair["samples"] = [{"T1": 1}]
        inv = da.inventory(m)
        pick = next(c for c in da.flux_candidates(inv) if not c["new"])
        port = (pick["con"], pick["fem"], pick["port"])
        plan = da.plan_pair(m, "q100", "q91", flux=port, inv=inv)
        assert not plan.get("error"), plan
        cr = dict(plan["creates"])
        new = cr["qubit_pairs.q100-q91"]
        assert new["coupler"]["opx_output"] == "#/wiring/qubit_pairs/q100-q91/flux_q100/opx_output"
        assert cr["wiring.qubit_pairs.q100-q91"]["flux_q100"]["opx_output"].endswith("/".join(map(str, port)))
        assert new["extras"] == {} and new["confusion"] is None and new["gef_result"] is None
        assert new["samples"] == [{"T1": None}]
        assert not any(p.startswith("ports.") for p in cr)
        # An arbitrary element reserves an output in the same shared inventory.
        m["other_element"] = {"output": "#/ports/analog_outputs/" + "/".join(map(str, port))}
        assert "not a free LF-FEM" in da.plan_pair(m, "q100", "q91", flux=port)["error"]

    def test_multiple_hardware_outputs_refused(self):
        s, w = make_chip(100, couplers=True)
        m = _merged_doc(s, w)
        tpid = da.pick_pair_template(m, "q100", "q91")
        m["wiring"]["qubit_pairs"][tpid]["extra"] = {"opx_output": "#/ports/analog_outputs/con1/2/1"}
        assert "single LF-FEM" in da.plan_pair(m, "q100", "q91")["error"]


def test_shared_member_never_reuses_a_template_operation():
    m, _, _ = _doc(20)
    pair = m['qubit_pairs']['q1-q2']
    m['qubits']['q1']['z']['operations']['shared_cz'] = {
        'length': '#/qubit_pairs/q1-q2/macros/cz_flattop/length'}
    pair['member_op'] = '#/qubits/q1/z/operations/shared_cz'
    p = da.plan_pair(m, 'q1', 'q20')
    assert p.get('error') and 'operation' in p['error'], p


@pytest.mark.parametrize('pointer', ['#/ports/mw_outputs/con1/1/2',
                                   '#/wiring/qubits/q1/xy/opx_output'])
def test_unmapped_port_pointer_is_refused(pointer):
    m, _, _ = _doc(20)
    d = da.qubit_defaults(m)
    pointer = pointer.replace('/q1/', '/' + d['template'] + '/')
    m['qubits'][d['template']]['xy']['custom_output'] = pointer
    _, p = TestQubitPlan()._plan(m)
    assert 'port' in p.get('error', '').lower(), p


def test_direct_standard_port_pointer_is_repointed():
    m, _, _ = _doc(20)
    d = da.qubit_defaults(m)
    m['qubits'][d['template']]['xy']['opx_output'] = '#/ports/mw_outputs/con1/1/2'
    _, p = TestQubitPlan()._plan(m)
    assert not p.get('error'), p
    q = dict(p['creates'])['qubits.' + d['name']]
    assert q['xy']['opx_output'] == '#/wiring/qubits/' + d['name'] + '/xy/opx_output'


def test_multiple_creates_share_one_missing_parent():
    assert da._with_parents({}, [('a.b.x', 1), ('a.b.y', 2)]) == [
        ('a', {'b': {'x': 1, 'y': 2}})]


def test_pair_plan_fills_missing_operation_parents():
    m, _, _ = _doc(20)
    del m['qubits']['q20']['z']['operations']
    p = da.plan_pair(m, 'q20', 'q11')
    assert not p.get('error'), p
    assert any(path == 'qubits.q20.z.operations' for path, _ in p['creates'])


def test_inventory_uses_a_nonreusable_store_identity(env, monkeypatch):
    from quam_state_manager.web import diagram_add_routes as D
    from quam_state_manager.web import routes as R
    st = env['store']
    D._INV_MEMO.clear()
    monkeypatch.setattr(D, 'id', lambda _: R._store_uid(st) + 1, raising=False)
    D._inv(st)
    assert D._INV_MEMO['k'][0] == (R._store_uid(st), st.mutation_seq)


@pytest.mark.parametrize('door', ['/state/sync?mode=reapply', '/state/sync?mode=discard'])
def test_inventory_refreshes_after_live_pull(env, door):
    from quam_state_manager.web import diagram_add_routes as D
    st = env['store']
    old = D._inv(st)
    key = D._INV_MEMO['k'][0]
    s = json.loads((env['chip'] / 'state.json').read_text(encoding='utf-8'))
    s['qubits']['q1']['custom_output'] = '#/ports/mw_outputs/spare/1/8'
    (env['chip'] / 'state.json').write_text(json.dumps(s), encoding='utf-8')
    r = env['c'].post(door)
    assert r.status_code == 200, r.data
    with env['app'].app_context():
        from quam_state_manager.web import routes as R
        st = R._active_ctx()['store']
    new = D._inv(st)
    assert D._INV_MEMO['k'][0] != key
    assert new is not old and ('mw_outputs', 'spare', 1, 8) in new['used']


def test_empty_chip_token_is_refused(env):
    _, form = _form(env['c'], env['store'])
    form['expect_chip'] = ''
    r = env['c'].post('/diagram-add/stage', data=form)
    assert r.status_code == 409 and not env['store'].change_log


def test_rollback_failure_names_what_remains(env, monkeypatch):
    _, form = _form(env['c'], env['store'])
    with env['app'].app_context():
        from quam_state_manager.web import routes as R
        mod = R._modifier()
    real = mod.create_subtree
    calls = []
    def fail_second(path, value, **kw):
        calls.append(path)
        if len(calls) == 2:
            raise ValueError('stage fault')
        return real(path, value, **kw)
    def fail_undo():
        raise ValueError('rollback fault')
    monkeypatch.setattr(mod, 'create_subtree', fail_second)
    monkeypatch.setattr(mod, 'undo', fail_undo)
    r = env['c'].post('/diagram-add/stage', data=form)
    text = r.get_data(as_text=True)
    assert r.status_code == 500 and 'qubits.q101' in text
    assert 'Nothing was added' not in text and 'rollback fault' in text


def test_rollback_covers_a_create_that_raises_after_logging(env, monkeypatch):
    _, form = _form(env['c'], env['store'])
    with env['app'].app_context():
        from quam_state_manager.web import routes as R
        mod = R._modifier()
    real = mod.create_subtree
    calls = []
    def fail_after_create(path, value, **kw):
        result = real(path, value, **kw)
        calls.append(path)
        if len(calls) == 2:
            raise ValueError('index update fault')
        return result
    monkeypatch.setattr(mod, 'create_subtree', fail_after_create)
    r = env['c'].post('/diagram-add/stage', data=form)
    assert r.status_code == 400
    assert 'q101' not in env['store'].merged['qubits']
    assert not env['store'].change_log


def test_a_rollback_exception_is_reported_even_after_its_last_pop(env, monkeypatch):
    _, form = _form(env['c'], env['store'])
    with env['app'].app_context():
        from quam_state_manager.web import routes as R
        mod = R._modifier()
    real_create, real_undo = mod.create_subtree, mod.undo
    def create(path, value, **kw):
        if env['store'].change_log:
            raise ValueError('stage fault')
        return real_create(path, value, **kw)
    def undo():
        real_undo()
        raise ValueError('rollback completion fault')
    monkeypatch.setattr(mod, 'create_subtree', create)
    monkeypatch.setattr(mod, 'undo', undo)
    r = env['c'].post('/diagram-add/stage', data=form)
    text = r.get_data(as_text=True)
    assert r.status_code == 500 and 'rollback completion fault' in text
    assert 'Still staged: none' in text and 'Nothing was added' not in text


class TestReadoutSlot:
    """W3-3: the add form copied the template qubit's readout frequency onto
    the same feedline, so the new resonator sat on top of the template's."""

    def _rr(self, m, q):
        return da._num(da._resolve(m, m["qubits"][q]["resonator"]["RF_frequency"],
                                   ("qubits", q, "resonator", "RF_frequency")))

    def test_the_default_is_a_free_slot_away_from_every_resonator(self):
        m, _s, _w = _doc(20)
        d = da.qubit_defaults(m)
        others = [self._rr(m, q) for q in d["members"]]
        assert d["readout_rf"] is not None and d["readout_note"] is None
        assert min(abs(d["readout_rf"] - f) for f in others if f is not None) >= da.READOUT_MIN_SEP_HZ

    def test_the_proposal_follows_the_feedline_spacing(self):
        assert da.propose_readout([7.0e9, 7.1e9, 7.2e9], 7.1e9, 7.0e9) == (7.3e9, None)
        # above the window: the slot below the lowest
        assert da.propose_readout([7.0e9, 7.1e9, 7.2e9], 6.85e9, 7.0e9)[0] == 6.9e9

    def test_no_slot_says_so_and_keeps_the_template(self):
        fs = [7.0e9 + i * 25e6 for i in range(33)]          # the whole window is full
        rf, note = da.propose_readout(fs, 7.4e9, 7.0e9)
        assert rf == 7.0e9 and note and "No free readout slot" in note

    def test_a_typed_readout_on_a_member_is_warned_in_the_plan(self):
        m, _s, _w = _doc(20)
        d = da.qubit_defaults(m)
        same = self._rr(m, d["template"])
        _d, p = TestQubitPlan()._plan(m, readout_rf=same)
        assert not p.get("error"), p
        assert any("same feedline" in n for n in p["summary"]["notes"])


class TestReadoutBand:
    """W3-3: a readout proposal stays inside the readout port's band."""

    INFO = {"lo": 7.4e9, "band": 2, "peer_band": 2, "qop": None}

    def test_an_lo_near_the_band_edge_proposes_an_in_band_slot(self):
        fs = [7.25e9, 7.35e9, 7.45e9]
        rf, note = da.propose_readout(fs, 7.4e9, 7.25e9, self.INFO)
        assert note is None and rf == 7.15e9

    def test_no_in_band_slot_says_so(self):
        fs = [7.0e9 + i * 20e6 for i in range(26)]           # 7.0 .. 7.5 GHz
        rf, note = da.propose_readout(fs, 7.4e9, 7.0e9, self.INFO)
        assert rf == 7.0e9 and note and "No free readout slot" in note

    def test_a_typed_readout_outside_the_band_is_warned(self):
        m, _s, _w = _doc(20)
        d = da.qubit_defaults(m)
        rp = da._wire_port(m, d["template"], "rr")
        da._port(m["ports"], *rp)["band"] = 1
        _d, p = TestQubitPlan()._plan(m, readout_rf=d["readout_rf"])
        assert not p.get("error"), p
        assert any("band" in n for n in p["summary"]["notes"])
