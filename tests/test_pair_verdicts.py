"""Pair runs are judged per pair, and a pair's `cz` alias decides its 2Q number.

A. check_fit over a run whose node targets qubit PAIRS listed the pair's
   member qubits too, and judged each one: the node reports neither an outcome
   nor a fit for a member qubit, so the gates read "the node's own analysis
   marked this target failed" on a run whose pair succeeded. The family match
   was a plain prefix test, so the ``chevron`` alias claimed ``chevron_11_20``
   (a different transition with different fit keys) as ``chevron_11_02``.

B. A pair holding two CZ macros, with ``macros.cz`` pointing at one of them,
   showed the BEST macro's fidelity on the topology edge and the Overview,
   while the 2Q panels showed the aliased one. ``query.pair_operational_gate``
   is the one rule: with an alias, that macro; with none, the best (as before).
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import query as Q
from quam_state_manager.core.autofit import families, gates
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]


# -- A: family matching --------------------------------------------------------

class TestFamilyMatchIsExact:
    def test_the_11_20_chevron_is_not_the_11_02_family(self):
        assert families.family_for("31_chevron_11_20") is None

    def test_the_11_02_chevron_still_matches(self):
        assert families.family_for("31_chevron_11_02").key == "chevron_11_02"
        assert families.family_for("2Q_19_chevron_1102").key == "chevron_11_02"
        assert families.family_for("31_chevron").key == "chevron_11_02"

    def test_a_named_suffix_still_matches(self):
        assert families.family_for("32a_cz_conditional_phase").key == "cz_conditional_phase"
        assert families.family_for("05_T1_vs_flux").key == "T1"

    def test_a_longer_word_is_not_the_shorter_name(self):
        assert families.family_for("05_T1x") is None


# -- A: which targets a run is judged on ---------------------------------------

def _pair_run(outcome="successful", fit_success=True):
    return {"qubits": ["q1", "q2"], "qubit_pairs": ["q1-q2"],
            "outcomes": {"q1-q2": outcome},
            "fit_results": {"q1-q2": {"optimal_amplitude": 0.19, "success": fit_success}}}


def _no_state(path):
    raise KeyError(path)


class TestNodeTargets:
    def test_a_pair_run_is_judged_per_pair(self):
        fam = families.family_for("32a_cz_conditional_phase")
        assert gates.node_targets(_pair_run(), fam) == ["q1-q2"]

    def test_a_pair_run_with_no_family_uses_the_outcomes(self):
        assert gates.node_targets(_pair_run(), None) == ["q1-q2"]

    def test_a_1q_run_is_unchanged(self):
        run = {"qubits": ["q1", "q2"], "qubit_pairs": [], "outcomes": {"q1": "successful"}}
        fam = families.family_for("04_power_rabi")
        assert gates.node_targets(run, fam) == ["q1", "q2"]

    def test_a_successful_pair_gets_no_invented_member_failures(self):
        fam = families.family_for("32a_cz_conditional_phase")
        run = _pair_run()
        v = gates.evaluate_run(run, fam, gates.node_targets(run, fam), current_value_of=_no_state)
        assert set(v) == {"q1-q2"}
        assert v["q1-q2"].checks["G1_node_outcome"] == "ok"
        assert v["q1-q2"].verdict != "fail"

    def test_a_pair_the_node_marked_failed_stays_failed(self):
        fam = families.family_for("32a_cz_conditional_phase")
        run = _pair_run(outcome="failed", fit_success=False)
        v = gates.evaluate_run(run, fam, gates.node_targets(run, fam), current_value_of=_no_state)
        assert v["q1-q2"].verdict == "fail"
        assert v["q1-q2"].failure_mode == "node_failed"


def _chip(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps({
        "qubits": {"q1": {"id": "q1"}, "q2": {"id": "q2"}},
        "qubit_pairs": {"q1-q2": {"id": "q1-q2", "qubit_control": "#/qubits/q1",
                                  "qubit_target": "#/qubits/q2", "macros": {}}}}), encoding="utf-8")
    (d / "wiring.json").write_text('{"wiring":{"qubits":{}}}', encoding="utf-8")
    return d


def _write_run(root: Path, rid: int, name: str, model: dict, outcomes: dict, fits: dict) -> Path:
    run = root / "2026-03-01" / f"#{rid}_{name}_1200{rid:02d}"
    _chip(run / "quam_state")
    (run / "node.json").write_text(json.dumps({
        "id": rid, "created_at": "2026-03-01T12:00:00+00:00",
        "metadata": {"name": name, "status": "finished"},
        "data": {"parameters": {"model": model}, "outcomes": outcomes}}), encoding="utf-8")
    (run / "data.json").write_text(json.dumps({"fit_results": fits}), encoding="utf-8")
    return run


class TestCheckFitRoute:
    @pytest.fixture
    def client(self, tmp_path):
        data = tmp_path / "data"
        _write_run(data, 1, "32a_cz_conditional_phase", {"qubit_pairs": ["q1-q2"]},
                   {"q1-q2": "successful"},
                   {"q1-q2": {"optimal_amplitude": 0.19, "success": True}})
        _write_run(data, 2, "31_chevron_11_20", {"qubit_pairs": ["q1-q2"]},
                   {"q1-q2": "successful"},
                   {"q1-q2": {"resonance_dv_v": 0.188, "success": True}})
        _write_run(data, 3, "04_power_rabi", {"qubits": ["q1", "q2"]},
                   {"q1": "successful", "q2": "failed"},
                   {"q1": {"opt_amp": 0.3, "success": True}, "q2": {"success": False}})
        _write_run(data, 4, "04_power_rabi", {}, {}, {})
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        assert c.post("/load", data={"folder": str(_chip(tmp_path / "loaded"))}).status_code in (200, 302)
        assert c.post("/workspace/add", data={"folder": str(data)}).status_code in (200, 302)
        return c

    def test_a_pair_run_is_judged_per_pair_only(self, client):
        r = client.get("/api/agent/check-fit/1").get_json()
        assert r["ok"] and r["family"] == "cz_conditional_phase"
        assert set(r["verdicts"]) == {"q1-q2"}, r["verdicts"]
        assert r["verdicts"]["q1-q2"]["verdict"] != "fail"

    def test_the_11_20_chevron_is_not_labelled_11_02(self, client):
        r = client.get("/api/agent/check-fit/2").get_json()
        assert r["ok"] and r["family"] is None, r
        assert r["outcomes"] == {"q1-q2": "successful"}

    def test_a_1q_run_still_judges_every_qubit(self, client):
        r = client.get("/api/agent/check-fit/3").get_json()
        assert r["family"] == "power_rabi"
        assert set(r["verdicts"]) == {"q1", "q2"}
        assert r["verdicts"]["q2"]["verdict"] == "fail"

    def test_a_run_with_nothing_judged_is_not_all_pass(self, client):
        r = client.get("/api/agent/check-fit/4").get_json()
        assert r["ok"] and r["family"] == "power_rabi"
        assert r["verdicts"] == {}
        assert r["all_pass"] is False
        assert "nothing is cleared for writing" in r["note"]

    def test_passing_targets_alone_are_all_pass(self, client):
        r = client.get("/api/agent/check-fit/1").get_json()
        if all(v["verdict"] == "pass" for v in r["verdicts"].values()):
            assert r["all_pass"] is True and "note" not in r
        else:
            assert r["all_pass"] is False

    def test_a_target_that_did_not_pass_is_named_not_to_write(self, client):
        r = client.get("/api/agent/check-fit/3").get_json()
        assert r["all_pass"] is False
        assert "q2" in r["do_not_write"] and r["do_not_write"]["q2"]
        assert all(r["verdicts"][t]["verdict"] != "pass" for t in r["do_not_write"])
        assert all(r["verdicts"][t]["verdict"] == "pass"
                   for t in set(r["verdicts"]) - set(r["do_not_write"]))


# -- B: the operational gate ---------------------------------------------------

class TestOperationalGate:
    def test_a_relative_alias(self):
        m = {"cz": "#./cz_unipolar", "cz_unipolar": {}, "cz_SNZ": {}}
        assert Q.pair_operational_gate("q1-q2", m) == "cz_unipolar"

    def test_an_absolute_alias_into_this_pair(self):
        m = {"cz": "#/qubit_pairs/q1-q2/macros/cz_SNZ", "cz_unipolar": {}, "cz_SNZ": {}}
        assert Q.pair_operational_gate("q1-q2", m) == "cz_SNZ"

    def test_no_alias_or_a_dangling_one_is_none(self):
        assert Q.pair_operational_gate("q1-q2", {"cz": {"fidelity": {}}}) is None
        assert Q.pair_operational_gate("q1-q2", {"cz_SNZ": {}}) is None
        assert Q.pair_operational_gate("q1-q2", {"cz": "#./gone", "cz_SNZ": {}}) is None
        assert Q.pair_operational_gate("q1-q2", {"cz": "#/qubit_pairs/q3-q4/macros/cz_SNZ",
                                                 "cz_SNZ": {}}) is None


def _two_macro_chip(tmp_path, alias):
    chip = tmp_path / "chip"
    chip.mkdir()
    macros = {"cz_unipolar": {"fidelity": {"InterleavedRB": 0.948}},
              "cz_SNZ": {"fidelity": {"InterleavedRB": 0.960}}}
    if alias:
        macros["cz"] = alias
    state = {
        "qubits": {"q1": {"id": "q1", "grid_location": "0,0"},
                   "q2": {"id": "q2", "grid_location": "1,0"}},
        "qubit_pairs": {"q1-q2": {"id": "q1-q2", "qubit_control": "#/qubits/q1",
                                  "qubit_target": "#/qubits/q2", "macros": macros}},
    }
    (chip / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (chip / "wiring.json").write_text('{"wiring":{"qubits":{}}}', encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    client = app.test_client()
    client.post("/load", data={"folder": str(chip)})
    return client.get("/api/topology").get_json()["edges"][0]


class TestTopologyEdgeFollowsTheAlias:
    def test_the_alias_to_the_worse_macro_is_shown(self, tmp_path):
        e = _two_macro_chip(tmp_path, "#./cz_unipolar")
        assert e["cz_fidelity"] == pytest.approx(0.948)
        assert e["best_gate"] == "cz_unipolar" and e["op_gate"] == "cz_unipolar"
        assert e["cz_fidelity_path"].endswith(".macros.cz_unipolar.fidelity.InterleavedRB")
        # every macro's rows still ship, for the labelled per-macro views
        assert {g["gate"] for g in e["gate_fidelities"]} == {"cz_unipolar", "cz_SNZ"}

    def test_no_alias_keeps_the_best_macro(self, tmp_path):
        e = _two_macro_chip(tmp_path, None)
        assert e["cz_fidelity"] == pytest.approx(0.960)
        assert e["best_gate"] == "cz_SNZ" and e["op_gate"] is None


def _node():
    return shutil.which("node")


@pytest.mark.skipif(not _node(), reason="node not installed")
def test_chip_status_follows_the_cz_alias():
    r = subprocess.run([_node(), str(ROOT / "tests" / "chip_status_cz_alias_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
                       env=dict(os.environ), timeout=120)
    if r.returncode == 2 and "jsdom not installed" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
