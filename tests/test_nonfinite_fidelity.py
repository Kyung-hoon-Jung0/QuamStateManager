"""A NaN / infinite stored number is "no measurement" (D-NF1..D-NF5).

* D-NF1: no JSON response carries a bare NaN / Infinity token; a finite
  payload is byte-identical to the default provider's.
* D-NF3: per-gate tie-break: newest time, then newest run id, then key order.
* D-NF4: a non-finite sibling row is marked not picked.
* D-NF5: a Trends series from one raw per-gate key names the key.
* The basis of a pick is stated where its source key is shown.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from flask.json.provider import DefaultJSONProvider

from quam_state_manager.core import query as Q
from quam_state_manager.core.cz_gate_plan import _fidelity as cz_plan_fidelity
from quam_state_manager.web.app import create_app
from tests.test_topology_pairs import _chip

ROOT = Path(__file__).resolve().parent.parent
BAD = [float("nan"), float("inf"), float("-inf")]


def _strict(text):
    def refuse(tok):
        raise ValueError("bare " + tok)
    return json.loads(text, parse_constant=refuse)


class TestJsonBoundary:
    @pytest.mark.parametrize("bad", BAD)
    @pytest.mark.parametrize("url", ["/api/topology", "/api/topology?layout=1"])
    def test_a_non_finite_pair_entry_is_valid_json(self, tmp_path, bad, url):
        c = _chip(tmp_path, {"InterleavedRB": bad, "IRB": 0.9434})
        r = c.get(url)
        assert r.status_code == 200
        body = _strict(r.get_data(as_text=True))
        rows = {g["metric"]: g for g in body["edges"][0]["gate_fidelities"]}
        assert rows["InterleavedRB"]["raw_value"] is None
        assert rows["InterleavedRB"]["raw_text"] == str(bad)
        assert body["edges"][0]["cz_fidelity"] == pytest.approx(0.9434)

    def test_a_non_finite_qubit_metric_is_valid_json(self, tmp_path):
        chip = tmp_path / "chip"
        chip.mkdir()
        state = {"qubits": {"q1": {"id": "q1", "grid_location": "0,0", "T1": float("nan"),
                                   "f_01": float("inf")}}}
        (chip / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (chip / "wiring.json").write_text('{"wiring":{"qubits":{}}}', encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
        c = app.test_client()
        c.post("/load", data={"folder": str(chip)})
        for url in ("/api/topology", "/api/topology?layout=1"):
            r = c.get(url)
            assert r.status_code == 200
            _strict(r.get_data(as_text=True))

    def test_a_finite_payload_is_byte_identical(self, tmp_path):
        c = _chip(tmp_path, {"InterleavedRB": 0.97, "IRB": 0.9434, "StandardRB": 0.9})
        app = c.application
        ours = [c.get(u).get_data() for u in ("/api/topology", "/api/topology?layout=1")]
        app.json = DefaultJSONProvider(app)
        plain = [c.get(u).get_data() for u in ("/api/topology", "/api/topology?layout=1")]
        assert ours == plain

    def test_the_provider_keeps_other_value_errors(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
        loop = []
        loop.append(loop)
        with pytest.raises(ValueError):
            app.json.dumps(loop)
        assert app.json.dumps({"a": [1.5, float("nan")]}) == '{"a": [1.5, null]}'


class TestTieBreak:
    T = "2026-01-01T00:00:00"

    def test_equal_times_fall_to_the_run_id(self):
        r = Q.pair_gate_fidelity({"InterleavedRB": 0.97, "InterleavedRB_updated_at": self.T,
                                  "InterleavedRB_load_id": 500,
                                  "IRB": 0.95, "IRB_updated_at": self.T, "IRB_load_id": 612})
        assert (r["key"], r["basis"]) == ("IRB", "newest run")

    def test_equal_time_and_run_fall_to_key_order(self):
        r = Q.pair_gate_fidelity({"InterleavedRB": 0.97, "InterleavedRB_updated_at": self.T,
                                  "InterleavedRB_load_id": 7,
                                  "IRB": 0.95, "IRB_updated_at": self.T, "IRB_load_id": 7})
        assert (r["key"], r["basis"]) == ("InterleavedRB", "key order")

    def test_a_time_on_one_key_only_lets_the_run_id_decide(self):
        r = Q.pair_gate_fidelity({"InterleavedRB": 0.97, "InterleavedRB_updated_at": self.T,
                                  "InterleavedRB_load_id": 5, "IRB": 0.95, "IRB_load_id": 9})
        assert (r["key"], r["basis"]) == ("IRB", "newest run")

    def test_a_newer_time_beats_a_newer_run(self):
        r = Q.pair_gate_fidelity({"InterleavedRB": 0.97, "InterleavedRB_updated_at": "2026-02-01T00:00:00",
                                  "InterleavedRB_load_id": 5,
                                  "IRB": 0.95, "IRB_updated_at": self.T, "IRB_load_id": 9})
        assert (r["key"], r["basis"]) == ("InterleavedRB", "newest time")


class TestPickMarks:
    @pytest.mark.parametrize("bad", BAD)
    def test_the_non_finite_sibling_is_not_picked(self, tmp_path, bad):
        e = _strict(_chip(tmp_path, {"InterleavedRB": bad, "IRB": 0.9434}).get("/api/topology")
                    .get_data(as_text=True))["edges"][0]
        rows = {g["metric"]: g for g in e["gate_fidelities"]}
        assert rows["InterleavedRB"]["gate_pick"] is False
        assert rows["InterleavedRB"]["superseded_by"] == "IRB"
        assert rows["IRB"]["gate_pick"] is True

    def test_a_finite_unphysical_value_keeps_its_handling(self, tmp_path):
        e = _strict(_chip(tmp_path, {"IRB": 1.53}).get("/api/topology").get_data(as_text=True))["edges"][0]
        (row,) = e["gate_fidelities"]
        assert row["physical"] is False and row["raw_value"] == 1.53 and "raw_text" not in row

    def test_the_report_labels_the_non_finite_row_and_states_the_basis(self, tmp_path):
        b = _chip(tmp_path, {"InterleavedRB": float("nan"), "IRB": 0.9434}) \
            .get("/chip-status/report").get_data(as_text=True)
        assert "not shown on the pages: IRB is this pulse's per-gate value (picked: the only entry that is a number (InterleavedRB holds no number)" in b

    def test_the_report_states_the_basis_of_the_pick(self, tmp_path):
        b = _chip(tmp_path, {"InterleavedRB": 0.97, "InterleavedRB_load_id": 5,
                             "IRB": 0.95, "IRB_load_id": 9}).get("/chip-status/report").get_data(as_text=True)
        assert "picked: newest run id" in b


class TestCzPlanBasis:
    def test_the_plan_row_carries_the_basis(self):
        out = cz_plan_fidelity({"InterleavedRB": 0.97, "InterleavedRB_load_id": 5,
                                "IRB": 0.95, "IRB_load_id": 9})
        assert out["InterleavedRB"]["basis_text"] == "picked: newest run id"
        assert cz_plan_fidelity({"IRB": 0.95})["InterleavedRB"]["basis_text"] == ""

    def test_the_tooltip_states_it(self):
        src = (ROOT / "quam_state_manager" / "web" / "templates" / "_cz_plan.html").read_text(encoding="utf-8")
        assert "{% if r.irb_basis %} ({{ r.irb_basis }}){% endif %}" in src


class TestTrendsLegend:
    def test_each_raw_key_series_names_its_key(self, tmp_path):
        from tests.test_trends_all_entities import _hot_cold_chip, _charts
        c = _hot_cold_chip(tmp_path, "both", {"macros.cz.fidelity.InterleavedRB": .90},
                           {"macros.cz.fidelity.IRB": .95}, steps=2)
        body = c.get("/topology/trends?metrics=&paths=qubit_pairs.*.macros.*.fidelity.InterleavedRB,"
                     "qubit_pairs.*.macros.*.fidelity.IRB").get_data(as_text=True)
        charts = {ch["metric"]: ch for ch in _charts(body)}
        a = charts["macros.*.fidelity.InterleavedRB"]["series"]
        b = charts["macros.*.fidelity.IRB"]["series"]
        assert a and all(s["entity"].endswith(" · InterleavedRB") for s in a)
        assert b and all(s["entity"].endswith(" · IRB") for s in b)
        default = c.get("/topology/trends").get_data(as_text=True)
        assert any(ch["metric"] == "gate_fidelity_resolved" for ch in _charts(default))


def _node():
    return shutil.which("node")


@pytest.mark.skipif(not _node(), reason="node not installed")
def test_chip_status_survives_a_non_finite_entry():
    r = subprocess.run([_node(), str(ROOT / "tests" / "chip_status_nonfinite_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
                       env=dict(os.environ), timeout=120)
    if r.returncode == 2 and "jsdom not installed" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr


class TestTheBasisWhenASiblingHoldsNoNumber:
    """A NaN / infinite sibling never competed, so "key order" is not why
    the other entry was picked, and the text must not contradict the pick."""

    def test_nan_under_the_lower_ranked_key(self):
        from quam_state_manager.core.query import gate_pick_basis_text, pair_gate_fidelity
        r = pair_gate_fidelity({"InterleavedRB": 0.9123, "IRB": float("nan")})
        assert (r["key"], r["basis"], r["no_number"]) == ("InterleavedRB", "only number", ["IRB"])
        assert gate_pick_basis_text(r) == (
            "picked: the only entry that is a number (IRB holds no number)")

    def test_inf_under_the_higher_ranked_key_never_reads_key_order(self):
        from quam_state_manager.core.query import gate_pick_basis_text, pair_gate_fidelity
        r = pair_gate_fidelity({"InterleavedRB": float("inf"), "IRB": 0.9010})
        assert (r["key"], r["basis"]) == ("IRB", "only number")
        text = gate_pick_basis_text(r)
        assert "key order" not in text and "InterleavedRB holds no number" in text

    def test_one_key_alone_states_no_basis(self):
        from quam_state_manager.core.query import gate_pick_basis_text, pair_gate_fidelity
        r = pair_gate_fidelity({"IRB": 0.95, "IRB_load_id": 612})
        assert r["basis"] == "key order" and r["no_number"] == []
        assert gate_pick_basis_text(r) == ""

    def test_two_numbers_and_a_nan_keep_the_real_basis(self):
        from quam_state_manager.core.query import pair_gate_fidelity
        r = pair_gate_fidelity({"InterleavedRB": 0.97, "IRB": 0.93,
                                "IRB_flattop": float("nan")})
        assert r["basis"] == "key order"

    def test_the_topology_rows_carry_the_same_text(self):
        from quam_state_manager.core import query
        rows = [{"level": "gate", "metric": "InterleavedRB"},
                {"level": "gate", "metric": "IRB"}]
        query._mark_gate_pick(rows, {"InterleavedRB": float("inf"), "IRB": 0.9010})
        assert [r["gate_pick"] for r in rows] == [False, True]
        assert all("holds no number" in r["gate_pick_basis_text"] for r in rows)
