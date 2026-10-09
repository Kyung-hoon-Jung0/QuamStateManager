"""S8 review P0-1: a panel's "Last changed / Written by" is about the number it shows.

A readout fidelity reads one diagonal cell of its confusion matrix and a 2Q RB
row reads one value of its block. The meta used to take the newest event over
EVERY leaf of the subtree, so an edit of the e-row, or a run that moved only
``alpha``, was named as the writer of the |g> fidelity / the gate fidelity it
never touched -- a wrong writer shown as fact. Each case here checks the
panel's meta against the value drawer of the very leaf the number is read
from: the two surfaces must name the same event.
"""

from __future__ import annotations

import re

from tests.test_hub_drawer import chip_state, make_app, patch, run, write_chip

META = "/topology/metric-meta"
PAIR = "qA1-qA2"
RB = f"qubit_pairs.{PAIR}.macros.cz.fidelity.StandardRB"
GEF = "qubits.qA1.resonator.gef_confusion_matrix"


def rb_state(*, agf=0.99, alpha=0.95, epg=0.01, **kw):
    s = chip_state(**kw)
    s["qubit_pairs"] = {PAIR: {"qubit_control": "#/qubits/qA1", "qubit_target": "#/qubits/qA2",
                               "macros": {"cz": {"fidelity": {"StandardRB": {
                                   "alpha": alpha, "average_gate_fidelity": agf,
                                   "error_per_gate": epg}}}}}}
    return s


def gef_state(cm, **kw):
    s = chip_state(**kw)
    s["qubits"]["qA1"]["resonator"]["gef_confusion_matrix"] = cm
    return s


def load(tmp_path, runs, live_state):
    data, live = tmp_path / "data", tmp_path / "chips" / "live"
    for i, (state, patches) in enumerate(runs, start=1):
        run(data, i, state, patches=patches)
    write_chip(live, live_state, data)
    app = make_app(tmp_path)
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return c


def drawer_newest(c, path):
    html = c.get("/field/history", query_string={"path": path}).data.decode()
    m = re.search(r'<tr class="vh-row [^"]*"\s+data-eid="(\d+)" data-provenance="([^"]+)">', html)
    assert m, html[:400]
    return {"eid": int(m.group(1)), "provenance": m.group(2)}


FULL_RB = [patch(f"{RB}.alpha", 0.96, 0.95), patch(f"{RB}.average_gate_fidelity", 0.995, 0.99),
           patch(f"{RB}.error_per_gate", 0.005, 0.01)]


def test_an_sm_edit_of_a_sibling_leaf_does_not_author_the_rb_value(tmp_path):
    s2 = rb_state(agf=0.995, alpha=0.96, epg=0.005)
    c = load(tmp_path, [(rb_state(), None), (s2, FULL_RB)], s2)
    assert c.post("/field/edit", data={"dot_path": f"{RB}.alpha", "value": "0.97"}).status_code == 200
    assert c.post("/state/apply-to-live", headers={"X-SM-Actor": "user-a"}).status_code == 200
    e = c.get(META).get_json()["p"]["2q:StandardRB:cz"][PAIR]
    d = drawer_newest(c, f"{RB}.average_gate_fidelity")
    assert e["eid"] == d["eid"] and e["provenance"] == d["provenance"] == "run_proven"
    assert e["run"] == 2 and "user-a" not in str(e.get("label")), e


def test_a_run_that_moved_only_alpha_is_not_the_writer_of_the_rb_value(tmp_path):
    s2 = rb_state(agf=0.995, alpha=0.96, epg=0.005)
    s3 = rb_state(agf=0.995, alpha=0.97, epg=0.005)
    c = load(tmp_path, [(rb_state(), None), (s2, FULL_RB), (s3, [patch(f"{RB}.alpha", 0.97, 0.96)])], s3)
    e = c.get(META).get_json()["p"]["2q:StandardRB:cz"][PAIR]
    d = drawer_newest(c, f"{RB}.average_gate_fidelity")
    assert e["eid"] == d["eid"] and e["run"] == 2 and (e.get("writer") or {}).get("run") == 2, e


def test_an_f_row_edit_moves_only_the_panels_that_read_it(tmp_path):
    cm1 = [[0.9, 0.05, 0.05], [0.1, 0.8, 0.1], [0.05, 0.15, 0.8]]
    cm2 = [[0.95, 0.03, 0.02], [0.1, 0.8, 0.1], [0.05, 0.15, 0.8]]
    p2 = [patch(f"{GEF}.0.0", 0.95, 0.9), patch(f"{GEF}.0.1", 0.03, 0.05), patch(f"{GEF}.0.2", 0.02, 0.05)]
    c = load(tmp_path, [(gef_state(cm1), None), (gef_state(cm2), p2)], gef_state(cm2))
    assert c.post("/field/edit", data={"dot_path": f"{GEF}.2.2", "value": "0.7"}).status_code == 200
    assert c.post("/state/apply-to-live", headers={"X-SM-Actor": "user-a"}).status_code == 200
    q = c.get(META).get_json()["q"]
    g, f, a = q["ro_fidelity_gef_g"]["qA1"], q["ro_fidelity_gef_f"]["qA1"], q["assignment_fidelity_gef"]["qA1"]
    assert g["eid"] == drawer_newest(c, f"{GEF}.0.0")["eid"] and g["run"] == 2, g
    edit = drawer_newest(c, f"{GEF}.2.2")
    assert f["eid"] == edit["eid"] and a["eid"] == edit["eid"], (f, a, edit)
    assert f["provenance"] == a["provenance"] == edit["provenance"] != "run_proven"
