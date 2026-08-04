"""FSP → pulse-amplitude compensation (docs/20 r12-B).

The hard contract: changing a port's full_scale_power_dbm NEVER silently
changes amplitudes — and never commits before the user saw the offer. The
/field/edit(+batch) gates 409 with the plan; only an explicit ack commits
(fsp_ack=comp → FSP + amps in ONE group = one Review bundle = one Ctrl+Z;
fsp_ack=solo → FSP alone). Physics: P_dBm = FSP + 20·log10|amp| ⇒
amp' = amp · 10^((FSP_old − FSP_new)/20); |amp'| > 1.0 clips at the DAC.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from quam_state_manager.core import mw_fem
from quam_state_manager.web.app import create_app

_WIRING = {
    "network": {"host": "3.3.3.3", "cluster_name": "F1"},
    "wiring": {
        "qubits": {
            "qA1": {"rr": {"opx_output": "#/ports/mw_outputs/con1/1/1"},
                    "xy": {"opx_output": "#/ports/mw_outputs/con1/1/2"},
                    "z": {"opx_output": "#/ports/analog_outputs/con1/2/1"}},
            "qA2": {"rr": {"opx_output": "#/ports/mw_outputs/con1/1/1"}},
        },
    },
}


def _state():
    return {
        "qubits": {
            "qA1": {
                "id": "qA1",
                "resonator": {
                    "opx_output": "#/wiring/qubits/qA1/rr/opx_output",
                    "operations": {
                        "readout": {"amplitude": 0.2, "length": 1000},
                        "const": {"amplitude": 0.1, "length": 100},
                        "ro_alias": "#./readout",
                        "weird": {"amplitude": "#/qubits/qA1/resonator/operations/readout/amplitude"},
                    },
                },
                "xy": {
                    "opx_output": "#/wiring/qubits/qA1/xy/opx_output",
                    "operations": {"x180": {"amplitude": 0.5, "length": 40}},
                },
                "z": {
                    "opx_output": "#/wiring/qubits/qA1/z/opx_output",
                    "operations": {"const": {"amplitude": 0.25}},
                },
            },
            "qA2": {
                "id": "qA2",
                "resonator": {
                    "opx_output": "#/wiring/qubits/qA2/rr/opx_output",
                    "operations": {"readout": {"amplitude": 0.6}},
                },
            },
        },
        "qubit_pairs": {},
        "active_qubit_names": ["qA1", "qA2"],
        "ports": {
            "mw_outputs": {"con1": {"1": {
                "1": {"band": 1, "full_scale_power_dbm": 0,
                      "upconverter_frequency": 5.0e9},
                "2": {"band": 2, "full_scale_power_dbm": 4,
                      "upconverter_frequency": 5.2e9},
            }}},
            "analog_outputs": {"con1": {"2": {"1": {"offset": 0.0}}}},
        },
    }


def _merged():
    m = dict(_state())
    m.update(_WIRING)
    return m


class TestPlanUnit:
    def test_shared_port_collects_all_channels(self):
        plan = mw_fem.fsp_compensation_plan(
            _merged(), "ports.mw_outputs.con1.1.1.full_scale_power_dbm", -6)
        assert plan is not None
        paths = {a["path"] for a in plan["amps"]}
        # rr port shared by qA1 + qA2 → both resonators' literal amps
        assert "qubits.qA1.resonator.operations.readout.amplitude" in paths
        assert "qubits.qA1.resonator.operations.const.amplitude" in paths
        assert "qubits.qA2.resonator.operations.readout.amplitude" in paths
        # never the xy port's ops, never LF flux
        assert not any("xy" in p or ".z." in p for p in paths)
        # alias op skipped silently; pointer amp disclosed
        assert not any(a["op"] == "ro_alias" for a in plan["amps"])
        assert any("weird" in s["path"] for s in plan["skipped"])

    def test_factor_and_clip(self):
        plan = mw_fem.fsp_compensation_plan(
            _merged(), "ports.mw_outputs.con1.1.1.full_scale_power_dbm", -6)
        f = 10.0 ** ((0 - (-6)) / 20.0)
        assert math.isclose(plan["factor"], f)
        ro = next(a for a in plan["amps"]
                  if a["path"].startswith("qubits.qA2"))
        assert math.isclose(ro["new"], 0.6 * f)
        assert ro["clips"] is True                 # 0.6 × ~1.995 ≈ 1.197 > 1
        assert plan["clip_count"] >= 1
        small = next(a for a in plan["amps"]
                     if a["path"].endswith("const.amplitude"))
        assert small["clips"] is False             # 0.1 × ~1.995 < 1

    def test_none_for_non_fsp_lf_or_noop(self):
        m = _merged()
        assert mw_fem.fsp_compensation_plan(
            m, "ports.mw_outputs.con1.1.1.band", 2) is None
        assert mw_fem.fsp_compensation_plan(
            m, "ports.analog_outputs.con1.2.1.offset", 0.1) is None
        assert mw_fem.fsp_compensation_plan(
            m, "ports.mw_outputs.con1.1.1.full_scale_power_dbm", 0) is None
        assert mw_fem.fsp_compensation_plan(
            m, "ports.mw_outputs.con1.1.1.full_scale_power_dbm", "abc") is None

    def test_range_warning(self):
        plan = mw_fem.fsp_compensation_plan(
            _merged(), "ports.mw_outputs.con1.1.1.full_scale_power_dbm", 25)
        assert plan["range_warn"] and "outside" in plan["range_warn"]


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    r = c.post("/load", data={"folder": str(live)})
    assert r.status_code in (200, 302)
    ctx = next(iter(app.config["contexts"].values()))
    return {"app": app, "client": c, "ctx": ctx}


_FSP = "ports.mw_outputs.con1.1.1.full_scale_power_dbm"


class TestNeverSilent:
    def test_field_edit_gates_without_ack(self, env):
        c, ctx = env["client"], env["ctx"]
        r = c.post("/field/edit", data={"dot_path": _FSP, "value": "-6"})
        assert r.status_code == 409
        body = r.get_json()
        assert body["fsp_compensation"]["clip_count"] >= 1
        assert len(body["fsp_compensation"]["amps"]) == 3
        assert len(ctx["store"].change_log) == 0, \
            "NOTHING commits before the user saw the offer"
        with ctx["store"]._lock:
            assert ctx["store"].state["ports"]["mw_outputs"]["con1"]["1"]["1"][
                "full_scale_power_dbm"] == 0

    def test_solo_ack_commits_fsp_only(self, env):
        c, ctx = env["client"], env["ctx"]
        r = c.post("/field/edit", data={"dot_path": _FSP, "value": "-6",
                                        "fsp_ack": "solo"})
        assert r.status_code == 200 and r.get_json()["ok"]
        with ctx["store"]._lock:
            st = ctx["store"].state
            assert st["ports"]["mw_outputs"]["con1"]["1"]["1"][
                "full_scale_power_dbm"] == -6
            assert st["qubits"]["qA2"]["resonator"]["operations"]["readout"][
                "amplitude"] == 0.6, "solo must not touch amplitudes"

    def test_comp_batch_is_one_undo_group(self, env):
        c, ctx = env["client"], env["ctx"]
        r0 = c.post("/field/edit", data={"dot_path": _FSP, "value": "-6"})
        plan = r0.get_json()["fsp_compensation"]
        updates = ([{"dot_path": _FSP, "value": "-6"}]
                   + [{"dot_path": a["path"], "value": str(a["new"])}
                      for a in plan["amps"]])
        r = c.post("/field/edit-batch", json={
            "updates": updates, "fsp_ack": "comp", "expect_chip": ""})
        assert r.status_code == 200 and r.get_json()["ok"], r.get_json()
        with ctx["store"]._lock:
            st = ctx["store"].state
            assert st["ports"]["mw_outputs"]["con1"]["1"]["1"][
                "full_scale_power_dbm"] == -6
            f = 10.0 ** (6 / 20.0)
            assert math.isclose(
                st["qubits"]["qA2"]["resonator"]["operations"]["readout"]["amplitude"],
                0.6 * f, rel_tol=1e-9)
        assert len(ctx["store"].change_log) == len(updates)
        # ONE Ctrl+Z reverts the whole thing (single group)
        u = c.post("/undo")
        assert u.status_code == 200
        assert len(ctx["store"].change_log) == 0
        with ctx["store"]._lock:
            st = ctx["store"].state
            assert st["ports"]["mw_outputs"]["con1"]["1"]["1"][
                "full_scale_power_dbm"] == 0
            assert st["qubits"]["qA2"]["resonator"]["operations"]["readout"][
                "amplitude"] == 0.6

    def test_batch_gates_without_ack(self, env):
        c, ctx = env["client"], env["ctx"]
        r = c.post("/field/edit-batch", json={
            "updates": [{"dot_path": _FSP, "value": "-6"},
                        {"dot_path": "qubits.qA1.xy.operations.x180.amplitude",
                         "value": "0.4"}],
            "expect_chip": ""})
        assert r.status_code == 409
        assert r.get_json()["fsp_compensation"] is not None
        assert len(ctx["store"].change_log) == 0, "the WHOLE batch waits"

    def test_non_fsp_edits_unaffected(self, env):
        c = env["client"]
        r = c.post("/field/edit", data={
            "dot_path": "qubits.qA1.xy.operations.x180.amplitude",
            "value": "0.45"})
        assert r.status_code == 200 and r.get_json()["ok"]

    def test_comp_batch_with_unrelated_row_is_one_undo_group(self, env):
        """The plot popup's Apply-All comp resend = FSP row + OTHER clicked
        fields + the compensated amps in ONE batch → one gid, one Ctrl+Z."""
        c, ctx = env["client"], env["ctx"]
        base = [{"dot_path": _FSP, "value": "-6"},
                {"dot_path": "qubits.qA1.xy.operations.x180.length",
                 "value": "48"}]
        r0 = c.post("/field/edit-batch", json={"updates": base,
                                               "expect_chip": ""})
        assert r0.status_code == 409
        plan = r0.get_json()["fsp_compensation"]
        updates = base + [{"dot_path": a["path"], "value": str(a["new"])}
                          for a in plan["amps"]]
        r = c.post("/field/edit-batch", json={
            "updates": updates, "fsp_ack": "comp", "expect_chip": ""})
        assert r.status_code == 200 and r.get_json()["ok"], r.get_json()
        with ctx["store"]._lock:
            st = ctx["store"].state
            assert st["ports"]["mw_outputs"]["con1"]["1"]["1"][
                "full_scale_power_dbm"] == -6
            assert st["qubits"]["qA1"]["xy"]["operations"]["x180"][
                "length"] == 48
        assert c.post("/undo").status_code == 200          # ONE Ctrl+Z
        assert len(ctx["store"].change_log) == 0
        with ctx["store"]._lock:
            st = ctx["store"].state
            assert st["ports"]["mw_outputs"]["con1"]["1"]["1"][
                "full_scale_power_dbm"] == 0
            assert st["qubits"]["qA1"]["xy"]["operations"]["x180"][
                "length"] == 40

    def test_js_wiring_pins(self):
        static = Path("quam_state_manager/web/static")
        app_js = (static / "app.js").read_text(encoding="utf-8")
        assert "_openFspPopup" in app_js
        assert "_fspCompUpdates" in app_js
        assert "Apply FSP + compensate" in app_js
        assert "the\n" not in ""  # noop
        for f in ("bulk-edit.js", "pair-edit.js", "all-values.js"):
            js = (static / f).read_text(encoding="utf-8")
            assert "fsp_compensation" in js, f"{f} must offer the popup"
            assert "fsp_ack" in js
        # docs/36 amendment: the plot-apply popup — BOTH handlers must route
        # the r12 fsp 409 and the r14 type_fix 409 (they used to dead-end on
        # the raw error string).
        i1 = app_js.index("function applyPlotRow")
        i2 = app_js.index("function applyAllPlotRows")
        i3 = app_js.index("function _markPlotRowApplied")
        for name, region in (("applyPlotRow", app_js[i1:i2]),
                             ("applyAllPlotRows", app_js[i2:i3])):
            for tok in ("fsp_compensation", "_openFspPopup", "fsp_ack",
                        "_fspCompUpdates", "type_fix", "_confirmTypeFix"):
                assert tok in region, f"{name} must handle the {tok} 409"
