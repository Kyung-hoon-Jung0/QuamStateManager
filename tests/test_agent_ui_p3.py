"""docs/272 -- four small Agent-UI findings (C-12, C-21, C-23, C-24).

The browser half (the cards, the steps, the per-tab name, the "N waiting"
links) is pinned under jsdom by ``tests/agent_ui_p3_selfcheck.cjs``, run by
the last test here. This file pins the server half of C-24: the unit a card
shows a value in comes from ``core.units`` -- the vocabulary the inspector and
the qubit/pair tables already use -- through the panel's feed, so agent.js
never holds a unit vocabulary of its own.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import approvals, units
from quam_state_manager.core.param_specs import _PAIR_PROPERTY_MAP, _QUBIT_PROPERTY_MAP
from tests.test_agent_panel import _chip, app, c, cal, fakes, inst, synth_folder  # noqa: F401  (fixtures)
from tests.test_agent_runs import HUMAN


class TestOneUnitVocabulary:
    @pytest.mark.parametrize("path, unit", [
        ("qubits.q1.T1", "µs"),
        ("qubits.q1.T2echo", "µs"),
        ("qubits.qA1.f_01", "GHz"),
        ("qubits.q1.resonator.f_01", "GHz"),                    # curated readout_frequency
        ("qubits.q1.anharmonicity", "MHz"),
        ("qubits.q1.xy.intermediate_frequency", "MHz"),
        ("qubits.q1.xy.operations.x180.length", "ns"),           # an alias path: the leaf rule
        ("qubits.q1.z.joint_offset", "V"),                       # curated z_joint_offset, NOT the bare leaf
        ("qubits.q1.z.opx_output.delay", "ns"),                  # curated z_delay_ns
        ("qubit_pairs.q1_q2.detuning", "V"),                     # QA F-24: a pair's own detuning is volts
        ("qubit_pairs.q1_q2.macros.cz.detuning", "MHz"),         # ... any other detuning is Hz
        ("qubit_pairs.q1_q2.coupler.decouple_offset", "V"),
    ])
    def test_a_known_path_shows_in_the_inspectors_unit(self, path, unit):
        spec = units.display_spec(path)
        assert spec is not None and spec["unit"] == unit, spec

    @pytest.mark.parametrize("path", [
        "qubits.q1.xy.operations.x180_DragCosine.amplitude", "qubits.q1.z.joint", "extras.data_folder",
        "network.port", "qubits.q1.resonator.operations.readout.integration_weights_angle", "",
    ])
    def test_an_unknown_path_gets_no_guessed_unit(self, path):
        assert units.display_spec(path) is None

    def test_a_pairs_own_detuning_is_volts_even_off_the_curated_map(self, monkeypatch):
        """The leaf rule keeps QA F-24 on its own: were `detuning` ever dropped
        from the curated pair rows, a pair's own detuning must still be volts."""
        monkeypatch.setattr(units, "_curated_path_keys", lambda: ())
        assert units.display_spec("qubit_pairs.q1_q2.detuning")["unit"] == "V"
        assert units.display_spec("qubit_pairs.q1_q2.zz.detuning")["unit"] == "MHz"

    def test_every_inspector_row_and_its_card_agree(self):
        """The card and the inspector row for the same leaf say the same unit:
        display_spec(path) is format_quantity(key) for every curated row."""
        checked = 0
        for is_pair, rows in ((False, _QUBIT_PROPERTY_MAP), (True, _PAIR_PROPERTY_MAP)):
            for _s, key, tmpl in rows:
                if not tmpl:
                    continue
                path = tmpl.format(name="qX_qY" if is_pair else "qX")
                ukey = units.pair_field_key(key) if (is_pair and path == f"qubit_pairs.qX_qY.{key}") else key
                fq = units.format_quantity(1.0, ukey)
                spec = units.display_spec(path)
                assert (spec and spec["unit"]) == (fq and fq[1]), (path, ukey, spec, fq)
                if spec:
                    shown = f"{1.0 * spec['scale']:.{spec['dp']}f}"
                    assert spec["unit"] == "ns" or shown == fq[0], (path, shown, fq)
                    assert spec["stored"] == units.stored_unit_label(ukey), path
                    checked += 1
        assert checked >= 15, checked


class TestTheFeedNamesTheUnit:
    def test_rows_carry_display_only_in_the_panels_feed(self, c, app):
        key = _chip(c)
        writes = [{"path": "qubits.qA1.T1", "old": 4.2e-5, "new": 3.3e-5},
                  {"path": "qubits.qA1.f_01", "old": 6.25e9, "new": 6.25001e9},
                  {"path": "qubits.qA1.xy.operations.x180_DragCosine.amplitude", "old": 0.1, "new": 0.11}]
        with app.app_context():
            ap = approvals.add(app.instance_path, key, kind="writes", node="05_power_rabi", targets=["qA1"],
                               writes=writes, reason="r", why_held="mode ask-writes", actor="by_claude")
            from quam_state_manager.web import agent_api
            reg = agent_api._registry()
            reg.runs["k-p3"] = {"key": "k-p3", "chip": key, "status": "done", "node": "05_power_rabi",
                                "targets": ["qA1"], "since": 1.0,
                                "result": {"status": "done", "writes": [dict(w) for w in writes]}}
        c.post("/api/agent/plans", json={"run_line": "/run 05_power_rabi qA1"}, headers=HUMAN)
        feed = c.get("/api/agent/chat/cards?after=0").get_json()
        live = feed["live"]
        aw = {w["path"]: w for w in next(a for a in live["approvals"] if a["id"] == ap["id"])["writes"]}
        assert aw["qubits.qA1.T1"]["display"] == units.display_spec("qubits.qA1.T1")
        assert aw["qubits.qA1.T1"]["display"]["unit"] == "µs"
        assert aw["qubits.qA1.f_01"]["display"]["unit"] == "GHz"
        assert "display" not in aw["qubits.qA1.xy.operations.x180_DragCosine.amplitude"], "no unit is guessed"
        rw = next(r for r in live["runs"] if r["key"] == "k-p3")["result"]["writes"]
        assert [w.get("display") for w in rw] == [units.display_spec(w["path"]) for w in writes]
        may = [m for p in live["plans"] for m in (p.get("may_change") or [])]
        assert may, "the plan lists what may change"
        assert all(m.get("display") == units.display_spec(m["path"]) for m in may), may
        # the stored records and the MCP-visible views are untouched
        with app.app_context():
            assert all("display" not in w for w in approvals.get(app.instance_path, key, ap["id"])["writes"])
            assert all("display" not in w for w in reg.runs["k-p3"]["result"]["writes"]), "the registry's dict"
        for r in c.get("/api/agent/runs/agent").get_json()["runs"]:
            assert all("display" not in w for w in (r.get("result") or {}).get("writes") or []), r
        for p in c.get("/api/agent/plans").get_json()["plans"]:
            assert all("display" not in m for m in p.get("may_change") or []), p
        for a in c.get("/api/agent/approvals").get_json()["pending"]:
            assert all("display" not in w for w in a.get("writes") or []), a


def test_agent_ui_p3_selfcheck():
    """The cards, the steps, the per-tab name and the "N waiting" links under jsdom."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([node, str(root / "tests" / "agent_ui_p3_selfcheck.cjs")], capture_output=True,
                       text=True, encoding="utf-8", timeout=180, cwd=str(root))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0 and " 0 failed" in r.stdout, r.stdout[-3000:] + r.stderr[-1500:]
