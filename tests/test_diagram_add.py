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
               "flux": f"{fl['con']}/{fl['fem']}/{fl['port']}"}


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
                                                       "target": "q100", "dry": "0"})
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
