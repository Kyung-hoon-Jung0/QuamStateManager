"""Chip Status overhaul — server side: get_topology() recency + summary block,
and the /topology route wiring the diagnostics health layer + thresholds.
"""

from __future__ import annotations

import json
from pathlib import Path

from quam_state_manager.core import chip_health
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.query import QueryEngine
from quam_state_manager.web.app import create_app


def _state():
    return {
        "qubits": {
            "qA1": {
                "id": "qA1",
                "f_01": 5.07e9,
                "anharmonicity": 2.12e8,
                "T1": 2.4e-5,
                "T2ramsey": 2.2e-5,
                "T2echo": 2.0e-5,
                "gate_fidelity": {"averaged": 0.9991,
                                  "averaged_updated_at": "2026-05-16 03:02:28 GMT+2"},
                "resonator": {"operations": {"readout": {"amplitude": 0.04, "length": 1000}}},
            },
            "qA2": {
                "id": "qA2",
                "f_01": 5.10e9,
                "T1": 1.8e-5,
                "gate_fidelity": {"averaged": 0.997,
                                  "averaged_updated_at": "2026-05-10 09:00:00 GMT+2"},
                "resonator": {"operations": {"readout": {"amplitude": 0.04}}},
            },
        },
        "qubit_pairs": {
            "qA2-qA1": {
                "id": "qA2-qA1",
                "qubit_control": "#/qubits/qA2",
                "qubit_target": "#/qubits/qA1",
                "macros": {
                    "cz": {"fidelity": {"Bell_State": {"Fidelity": 0.96,
                                                       "updated_at": "2026-05-14 12:00:00 GMT+2"},
                                        "StandardRB_load_id": 529}},
                },
                "coupler": None, "detuning": None, "confusion": None,
                "mutual_flux_bias": None,
            },
        },
    }


def _wiring():
    return {"wiring": {"qubits": {}}, "network": {"host": "10.1.1.1"}}


# --- get_topology data ------------------------------------------------------


def test_get_topology_adds_node_recency():
    eng = QueryEngine(QuamStore.from_dicts(_state(), _wiring()))
    topo = eng.get_topology()
    n = {x["id"]: x for x in topo["nodes"]}
    assert n["qA1"]["gate_fidelity_updated_at"] == chip_health.epoch_ms("2026-05-16 03:02:28 GMT+2")
    # last_calibrated is the freshest *updated_at in the qubit subtree
    assert n["qA1"]["last_calibrated"] == chip_health.epoch_ms("2026-05-16 03:02:28 GMT+2")
    assert n["qA2"]["gate_fidelity_updated_at"] == chip_health.epoch_ms("2026-05-10 09:00:00 GMT+2")


def test_get_topology_adds_edge_provenance_and_recency():
    eng = QueryEngine(QuamStore.from_dicts(_state(), _wiring()))
    topo = eng.get_topology()
    e = topo["edges"][0]
    assert e["cz_fidelity"] == 0.96
    assert e["cz_load_id"] == 529           # provenance → dataset run #529
    # Per-metric recency: the CZ fidelity's OWN timestamp (best gate's Bell_State),
    # both as an edge field and inside the metric record.
    ts = chip_health.epoch_ms("2026-05-14 12:00:00 GMT+2")
    assert e["cz_fidelity_updated_at"] == ts
    assert e["metrics"]["cz_fidelity"]["updated_at"] == ts
    assert e["last_calibrated"] == ts


def test_metric_records_carry_honest_recency_and_no_fake_sigma():
    # gate_fidelity_avg + cz_fidelity carry their OWN updated_at; T1 etc. have no
    # stored per-metric timestamp, so their record updated_at stays None (never
    # back-filled from an unrelated calibration). sigma is never fabricated.
    eng = QueryEngine(QuamStore.from_dicts(_state(), _wiring()))
    topo = eng.get_topology()
    n = {x["id"]: x for x in topo["nodes"]}["qA1"]
    assert n["metrics"]["gate_fidelity_avg"]["updated_at"] == chip_health.epoch_ms("2026-05-16 03:02:28 GMT+2")
    assert n["metrics"]["T1"]["updated_at"] is None
    assert n["metrics"]["T1"]["sigma"] is None
    assert n["metrics"]["gate_fidelity_avg"]["sigma"] is None


def test_get_topology_summary_block():
    eng = QueryEngine(QuamStore.from_dicts(_state(), _wiring()))
    topo = eng.get_topology()
    s = topo["summary"]
    assert s["qubit_count"] == 2 and s["pair_count"] == 1
    # aggregates over numeric node metrics
    assert s["nodes"]["T1"]["count"] == 2
    assert s["nodes"]["T1"]["min"] == 1.8e-5 and s["nodes"]["T1"]["max"] == 2.4e-5
    # recency epochs must NOT pollute the metric aggregates
    assert "gate_fidelity_updated_at" not in s["nodes"]
    assert "last_calibrated" not in s["nodes"]
    # oldest/newest calibration spans the two qubit timestamps
    assert s["oldest_calibration"] == chip_health.epoch_ms("2026-05-10 09:00:00 GMT+2")
    assert s["newest_calibration"] == chip_health.epoch_ms("2026-05-16 03:02:28 GMT+2")


def test_get_topology_node_skip_keeps_real_metrics():
    eng = QueryEngine(QuamStore.from_dicts(_state(), _wiring()))
    s = eng.get_topology()["summary"]
    # f_01 is a real metric and should be aggregated
    assert "f_01" in s["nodes"]
    # ids / chain / grid_location are not metrics
    for junk in ("id", "chain", "grid_location"):
        assert junk not in s["nodes"]


# --- /topology route --------------------------------------------------------


def test_topology_route_ships_health_layer(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    client = app.test_client()
    client.post("/load", data={"folder": str(tmp_path)})
    body = client.get("/topology").get_data(as_text=True)
    assert "summary" in body                 # topology_json carries the summary block
    assert "gate_fidelity_updated_at" in body
    # thresholds seed is shipped for the client verdict
    assert "assignment_fidelity" in body
    assert "metrics" in body                 # the MetricRecord map ships to the client
    # history_count is shipped so the client can gate the lazy sparkline fetch
    assert "historyCount" in body
    # keyboard-nav scaffolding: the scroll-spy is a tablist + the hint mentions arrows
    assert 'role="tablist"' in body and body.count('role="tab"') >= 8
    assert "<kbd>Tab</kbd>" in body


# --- Foundation + Phase 0: MetricRecord contract + trust floor --------------


def _state_with_bad_data():
    st = _state()
    # qA1 gets an impossible 153% 1Q fidelity and a negative T2echo (failed fits)
    st["qubits"]["qA1"]["gate_fidelity"]["averaged"] = 1.5345
    st["qubits"]["qA1"]["T2echo"] = -4.7e-4
    # qA2 gets a DANGLING pointer for f_01 (points at a qubit that doesn't exist)
    st["qubits"]["qA2"]["f_01"] = "#/qubits/ghost/f_01"
    return st


def test_node_carries_metric_records():
    topo = QueryEngine(QuamStore.from_dicts(_state(), _wiring())).get_topology()
    n = {x["id"]: x for x in topo["nodes"]}["qA1"]
    assert "metrics" in n and "T1" in n["metrics"]
    rec = n["metrics"]["gate_fidelity_avg"]
    assert rec["physical"] is True and rec["verdict"] == "pass"
    # scalar stays for back-compat (histograms / chip-compare read it)
    assert n["gate_fidelity_avg"] == 0.9991


def test_unphysical_quarantined_in_record_and_summary():
    topo = QueryEngine(QuamStore.from_dicts(_state_with_bad_data(), _wiring())).get_topology()
    n = {x["id"]: x for x in topo["nodes"]}["qA1"]
    gf = n["metrics"]["gate_fidelity_avg"]
    assert gf["physical"] is False and gf["value"] is None and gf["raw"] == 1.5345
    assert gf["verdict"] is None                       # never pass-green
    t2 = n["metrics"]["T2echo"]
    assert t2["physical"] is False and t2["value"] is None and t2["raw"] == -4.7e-4
    # summary excludes them from stats but counts them as "bad"
    s = topo["summary"]["nodes"]
    assert s["gate_fidelity_avg"]["bad"] == 1
    # the only physical 1Q fidelity is qA2's 0.997 → that's the min AND max
    assert s["gate_fidelity_avg"]["measured"] == 1
    assert s["gate_fidelity_avg"]["max"] == 0.997 and s["gate_fidelity_avg"]["min"] == 0.997


def test_dangling_pointer_is_unresolved_not_nan():
    topo = QueryEngine(QuamStore.from_dicts(_state_with_bad_data(), _wiring())).get_topology()
    n = {x["id"]: x for x in topo["nodes"]}["qA2"]
    f = n["metrics"]["f_01"]
    assert f["unresolved"] is True and f["value"] is None     # not the raw '#/...' string
    s = topo["summary"]["nodes"]["f_01"]
    assert s["unresolved"] == 1 and s["missing"] == 1         # counted missing, not measured
    assert s["measured"] == 1                                  # qA1's real f_01


def test_honest_counts_distinguish_missing_from_bad():
    # chi is absent on all qubits (missing); T2echo on qA1 is bad; both differ.
    topo = QueryEngine(QuamStore.from_dicts(_state_with_bad_data(), _wiring())).get_topology()
    s = topo["summary"]["nodes"]
    assert s["chi"]["missing"] == 2 and s["chi"]["bad"] == 0 and s["chi"]["measured"] == 0
    # qA1's T2echo is bad (-473µs); qA2 has no T2echo (missing) → measured 0
    assert s["T2echo"]["bad"] == 1 and s["T2echo"]["missing"] == 1 and s["T2echo"]["measured"] == 0
    assert s["T2echo"]["total"] == 2


def test_unnormalized_confusion_matrix_yields_no_readout_fidelity():
    from quam_state_manager.core import query as q
    # valid row-stochastic → a fidelity; unnormalized (counts) → None (not garbage)
    assert q._assignment_fidelity([[0.98, 0.02], [0.05, 0.95]]) == 0.965
    assert q._assignment_fidelity([[98, 2], [5, 95]]) is None        # counts, rows sum 100
    assert q._assignment_fidelity([[0.5, 0.2], [0.1, 0.3]]) is None  # rows don't sum ~1
    assert q._cm_diag([[98, 2], [5, 95]], 0) is None
