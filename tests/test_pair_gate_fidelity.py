"""One per-gate fidelity per CZ macro, the same on every surface (gl-1h8), an
unphysical one shown rather than dropped (gl-6ot), and one count of issues.

A macro's fidelity block can carry two keys that are both per-gate
(``InterleavedRB`` and a lab alias ``IRB``) and that disagree. Before this the
topology edge kept the LARGER one, the pair inspector read only
``InterleavedRB``, the CZ gate plan read only ``InterleavedRB`` and the 2Q Fid.
chart / Overview kept the larger per pulse -- four answers to one question.
``query.pair_gate_fidelity`` is now the one answer.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import diagnostics, pair_map_panel
from quam_state_manager.core import query as Q
from quam_state_manager.core.cz_gate_plan import _fidelity as cz_plan_fidelity
from quam_state_manager.web.app import create_app
from tests.test_topology_pairs import _chip, _edge

ROOT = Path(__file__).resolve().parents[1]

# a pair holding both keys with different values, as a real chip did
BOTH = {"InterleavedRB": 0.9073, "IRB": 0.9434, "IRB_alpha": 0.83,
        "StandardRB": 0.8978, "StandardRB_load_id": 77}


# -- the resolver --------------------------------------------------------------

class TestResolver:
    def test_key_order_without_times_and_never_the_larger(self):
        r = Q.pair_gate_fidelity(BOTH)
        assert r["key"] == "InterleavedRB" and r["value"] == 0.9073   # 0.9434 is larger
        assert r["basis"] == "key order"
        assert r["keys"] == ["InterleavedRB", "IRB"]

    def test_the_smaller_alias_does_not_win_either(self):
        r = Q.pair_gate_fidelity({"InterleavedRB": 0.95, "IRB": 0.90})
        assert r["key"] == "InterleavedRB" and r["value"] == 0.95

    def test_newest_time_wins_over_the_key_order(self):
        fid = {"InterleavedRB": 0.90, "InterleavedRB_updated_at": "2026-09-06 07:33:36 GMT+3",
               "IRB": 0.95, "IRB_updated_at": "2026-09-07 07:33:36 GMT+3"}
        r = Q.pair_gate_fidelity(fid)
        assert (r["key"], r["basis"]) == ("IRB", "newest time")
        fid["InterleavedRB_updated_at"] = "2026-09-08 07:33:36 GMT+3"
        assert Q.pair_gate_fidelity(fid)["key"] == "InterleavedRB"

    def test_a_time_on_one_key_only_is_not_a_comparison(self):
        fid = {"InterleavedRB": 0.90, "IRB": 0.95, "IRB_updated_at": "2026-09-07 07:33:36 GMT+3"}
        assert Q.pair_gate_fidelity(fid)["key"] == "InterleavedRB"

    def test_newest_run_id_wins_when_every_key_has_one(self):
        fid = {"InterleavedRB": 0.90, "InterleavedRB_load_id": 12, "IRB": 0.95, "IRB_load_id": 40}
        r = Q.pair_gate_fidelity(fid)
        assert (r["key"], r["basis"]) == ("IRB", "newest run")

    def test_nested_value_and_time(self):
        fid = {"InterleavedRB": {"fidelity": 0.91, "updated_at": "2026-01-02 00:00:00"},
               "IRB": {"fidelity": 0.93, "updated_at": "2026-01-01 00:00:00"}}
        r = Q.pair_gate_fidelity(fid)
        assert (r["key"], r["value"]) == ("InterleavedRB", 0.91)

    def test_conflict_list_in_precedence_order_and_tolerance(self):
        assert Q.pair_gate_fidelity(BOTH)["conflict"] == [("InterleavedRB", 0.9073), ("IRB", 0.9434)]
        same = Q.pair_gate_fidelity({"InterleavedRB": 0.95, "IRB": 0.95 + 1e-9})
        assert same["conflict"] == []
        assert Q.gate_conflict_text(Q.pair_gate_fidelity(BOTH)["conflict"]) == \
            "2 IRB values on this pair: InterleavedRB 0.9073, IRB 0.9434"

    def test_clifford_bell_and_decay_are_never_candidates(self):
        assert Q.pair_gate_fidelity({"StandardRB": 0.9, "Bell_State": {"Fidelity": 0.97},
                                     "InterleavedRB_alpha": 0.8}) is None
        assert Q.pair_gate_fidelity({}) is None and Q.pair_gate_fidelity(None) is None

    def test_an_unphysical_pick_is_returned_marked_not_replaced(self):
        r = Q.pair_gate_fidelity({"IRB": 1.5345})
        assert r["value"] == 1.5345 and r["physical"] is False


def _cz_chip(tmp_path, fid):
    """A pair whose macro is CZ-shaped (a flux pulse), so the inspector lists it."""
    chip = tmp_path / "chip"
    chip.mkdir(parents=True)
    state = {
        "qubits": {"q1": {"id": "q1", "grid_location": "0,0"},
                   "q2": {"id": "q2", "grid_location": "1,0"}},
        "qubit_pairs": {"q1-q2": {
            "id": "q1-q2", "qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
            "macros": {"cz": {"flux_pulse_qubit": {"amplitude": 0.1, "length": 40},
                              "fidelity": fid}}}},
    }
    (chip / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (chip / "wiring.json").write_text('{"wiring":{"qubits":{}}}', encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    client = app.test_client()
    client.post("/load", data={"folder": str(chip)})
    return client


# -- the surfaces --------------------------------------------------------------

class TestTopologyEdge:
    def test_the_edge_takes_the_resolver_pick_and_names_the_conflict(self, tmp_path):
        e = _edge(_chip(tmp_path, dict(BOTH)))
        assert e["cz_fidelity"] == pytest.approx(0.9073)
        assert e["cz_fidelity_path"].endswith(".fidelity.InterleavedRB")
        assert e["fidelity_source"] == "interleaved_rb"
        assert e["fidelity_conflict"] == [["InterleavedRB", 0.9073], ["IRB", 0.9434]]

    def test_rows_carry_the_pick(self, tmp_path):
        rows = {r["metric"]: r for r in _edge(_chip(tmp_path, dict(BOTH)))["gate_fidelities"]}
        assert rows["InterleavedRB"]["gate_pick"] is True
        assert rows["IRB"]["gate_pick"] is False and rows["IRB"]["superseded_by"] == "InterleavedRB"
        assert rows["IRB"]["gate_conflict"] == [["InterleavedRB", 0.9073], ["IRB", 0.9434]]
        assert "gate_pick" not in rows["StandardRB"]

    def test_one_key_rows_are_unchanged(self, tmp_path):
        e = _edge(_chip(tmp_path, {"IRB": 0.94}))
        assert e["cz_fidelity"] == pytest.approx(0.94) and e["fidelity_conflict"] == []
        (row,) = e["gate_fidelities"]
        assert not ({"gate_pick", "superseded_by", "gate_conflict"} & set(row))

    def test_standard_rb_only_still_has_no_per_gate_value(self, tmp_path):
        e = _edge(_chip(tmp_path, {"StandardRB": 0.9, "StandardRB_load_id": 3}))
        assert e["cz_fidelity"] is None and e["fidelity_source"] is None

    def test_bell_still_ahead_of_irb(self, tmp_path):
        e = _edge(_chip(tmp_path, {"Bell_State": {"Fidelity": 0.97}, **BOTH}))
        assert e["cz_fidelity"] == pytest.approx(0.97) and e["fidelity_source"] == "macro"

    def test_an_unphysical_irb_never_colours_the_edge(self, tmp_path):
        e = _edge(_chip(tmp_path, {"IRB": 1.5345}))
        assert e["cz_fidelity"] is None
        (row,) = e["gate_fidelities"]
        assert row["physical"] is False and row["raw_value"] == 1.5345


class TestPairInspector:
    def test_the_row_edits_the_picked_key_and_flags_the_conflict(self, tmp_path):
        c = _cz_chip(tmp_path, dict(BOTH))
        html = c.get("/pair/q1-q2").get_data(as_text=True)
        assert "qubit_pairs.q1-q2.macros.cz.fidelity.InterleavedRB" in html
        assert 'data-fid-conflict="1"' in html
        assert "2 IRB values on this pair: InterleavedRB 0.9073, IRB 0.9434" in html

    def test_a_lab_alias_alone_now_shows(self, tmp_path):
        html = _cz_chip(tmp_path, {"IRB": 0.94}).get("/pair/q1-q2").get_data(as_text=True)
        assert "qubit_pairs.q1-q2.macros.cz.fidelity.IRB" in html
        assert 'data-fid-conflict' not in html      # one key: no clutter
        assert 'value="0.94"' in html               # its value, not "not set"

    def test_get_pair_value_is_the_pick(self, tmp_path):
        c = _cz_chip(tmp_path, dict(BOTH))
        with c.application.app_context():
            from quam_state_manager.web import routes
            pair = routes._engine().get_pair("q1-q2")
        assert pair["cz_interleaved_rb"] == 0.9073
        c2 = _cz_chip(tmp_path / "alias", {"IRB": 0.94})
        with c2.application.app_context():
            from quam_state_manager.web import routes
            assert routes._engine().get_pair("q1-q2")["cz_interleaved_rb"] == 0.94


class TestCzPlanAndMapPanel:
    def test_cz_plan_fidelity_reads_the_resolver(self):
        out = cz_plan_fidelity(dict(BOTH))
        assert out["InterleavedRB"]["value"] == 0.9073 and out["InterleavedRB"]["key"] == "InterleavedRB"
        assert out["InterleavedRB"]["conflict"] == [["InterleavedRB", 0.9073], ["IRB", 0.9434]]
        assert cz_plan_fidelity({"IRB": 0.94})["InterleavedRB"]["key"] == "IRB"
        assert out["StandardRB"]["value"] == 0.8978

    def test_map_panel_shows_one_per_gate_line_with_the_mark(self, tmp_path):
        e = _edge(_chip(tmp_path, dict(BOTH)))
        rows = pair_map_panel.pair_rows(e, {})
        line = next(r for r in rows if r[0] == "cz")[1]
        per_gate = line.split(" \u00b7 ")[0]
        assert per_gate.startswith("IRB 0.9073 from InterleavedRB")      # one per-gate entry
        assert line.count("IRB 0.9434") == 1                             # only in the conflict text
        assert "2 IRB values on this pair" in line


class TestReport:
    def test_report_lists_both_numbers_labels_the_unused_one(self, tmp_path):
        b = _chip(tmp_path, dict(BOTH)).get("/chip-status/report").get_data(as_text=True)
        assert "not shown on the pages: InterleavedRB is this pulse" in b
        assert "2 IRB values on this pair: InterleavedRB 0.9073, IRB 0.9434" in b

    def test_report_keeps_the_unphysical_raw(self, tmp_path):
        b = _chip(tmp_path, {"IRB": 1.5345}).get("/chip-status/report").get_data(as_text=True)
        assert "unphysical" in b and "153.45" not in b


# -- one count of issues -------------------------------------------------------

class TestIssueCount:
    def _findings(self):
        F = diagnostics.Finding
        return [F("warning", "a", "x", "w1"), F("warning", "a", "y", "w2"),
                F("recommendation", "connectivity_port_sharing", "z", "r1"),
                F("info", "b", "w", "i1")]

    def test_summarize_counts_a_recommendation_as_advisory(self):
        s = diagnostics.summarize(self._findings())
        assert (s["warning"], s["advisory"], s["info"]) == (2, 1, 1)

    def test_the_diagnostics_page_counts_the_same_warnings(self, tmp_path):
        c = _chip(tmp_path, {"IRB": 0.94})
        with c.application.test_request_context():
            from flask import render_template
            fs = self._findings()
            html = render_template("_diagnostics_list.html", findings=fs,
                                   diag_summary=diagnostics.summarize(fs), allow_jump=False,
                                   allow_fix=False)
        assert ">2 warnings<" in html and ">1 recommendation<" in html


# -- the page (the shipped JS under jsdom) -------------------------------------

def _node():
    return shutil.which("node")


@pytest.mark.skipif(not _node(), reason="node not installed")
def test_chip_status_shows_the_pick_and_the_unphysical_value():
    r = subprocess.run([_node(), str(ROOT / "tests" / "chip_status_gate_pick_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
                       env=dict(os.environ), timeout=120)
    if r.returncode == 2 and "jsdom not installed" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
