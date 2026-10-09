"""A run whose saved state names another chip is never this chip's value.

A data folder linked to chip "alpha" can hold a run that "beta" saved (a
shared lab root). The run sync keeps it and flags it CHIP_UNCERTAIN; the
By-run columns, the calibration age and the Versions baseline already left
such runs out, but the value series showed beta's value as alpha's NEWEST
point (with a Use button), the metric meta returned it and Trends charted it.
Now the folder's view leaves the run out of every value answer, counted and
said in a note (Versions never offered such a run as a version: the list
says it was left out); and alpha's own
next run is re-diffed against alpha's previous state, so a change that
beta's run happened to mask is still found."""

from __future__ import annotations

# S10 C7: old -> new, remove callerless snapshot hooks and retain ledger behavior.

import json
from pathlib import Path

from quam_state_manager.web.app import create_app
from tests.ledger_fixture import declare_root

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"},
           "ports": {"mw_outputs": {"con1": {"1": {"2": {"band": 1}}}}}}


def _state(f01: float, chip: str) -> dict:
    return {"qubits": {"qA1": {"id": "qA1", "f_01": f01, "anharmonicity": 200e6}},
            "qubit_pairs": {}, "active_qubit_names": ["qA1"], "extras": {"chip_name": chip}}


def _write_chip(folder: Path, state: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


def _seed_run(root: Path, run_id: int, state: dict, hhmmss: str) -> None:
    run = root / "2026-07-29" / f"#{run_id}_08_qubit_spectroscopy_{hhmmss}"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": "08_qubit_spectroscopy", "status": "successful",
                     "run_start": f"2026-07-29T{hhmmss[:2]}:{hhmmss[2:4]}:00",
                     "run_end": f"2026-07-29T{hhmmss[:2]}:{hhmmss[2:4]}:01"},
        "data": {"parameters": {"model": {"qubits": ["qA1"]}}, "outcomes": {}},
        "id": run_id, "parents": [], "created_at": f"2026-07-29T{hhmmss[:2]}:{hhmmss[2:4]}:00",
    }), encoding="utf-8")
    (run / "data.json").write_text("{}", encoding="utf-8")
    _write_chip(run / "quam_state", state)


def _open(tmp_path, runs):
    root = tmp_path / "data"
    for rid, f01, chip, hhmmss in runs:
        _seed_run(root, rid, _state(f01, chip), hhmmss)
    live = tmp_path / "chips" / "a"
    _write_chip(live, _state(7.1e9, "alpha"))
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    c.post("/workspace/add", data={"folder": str(root)})
    declare_root(c, root)
    return c


def _points(c):
    body = c.get("/api/agent/field-history?path=qubits.qA1.f_01").get_json()["history"]
    return body, [(p["value"], p["provenance"]) for p in body["points"]]


def test_another_chips_run_is_never_this_chips_newest_value(tmp_path):
    c = _open(tmp_path, [(31, 7.1e9, "alpha", "010000"), (32, 7.2e9, "beta", "020000")])
    body, pts = _points(c)
    assert all(v != 7.2e9 for v, _p in pts), pts
    assert all(p != ("run_" + "uncertain_chip") for _v, p in pts), pts
    assert body.get("other_chip_hidden") == 1
    drawer = c.get("/field/history?path=qubits.qA1.f_01").data.decode()
    assert 'data-value="7200000000.0"' not in drawer, "no Use button for another chip's value"
    assert "does not match this chip" in drawer, "the left-out run is said, not silent"
    assert 'data-note="other_chip"' in drawer
    meta = c.get("/topology/metric-meta").get_json()
    assert (meta["q"].get("f_01", {}).get("qA1") or {}).get("value") != 7.2e9
    trends = c.get("/topology/trends?metrics=f_01").get_data(as_text=True)
    assert "7200000000" not in trends and "7.2e9" not in trends and "7.2e+09" not in trends


def test_a_change_another_chips_run_masked_is_still_found(tmp_path):
    """alpha 7.1e9 -> beta's run 7.2e9 -> alpha's own run saves 7.2e9. The
    stored row of alpha's second run is empty (beta held 7.2e9 before it);
    alpha's view re-diffs it against alpha's previous state."""
    c = _open(tmp_path, [(31, 7.1e9, "alpha", "010000"), (32, 7.2e9, "beta", "020000"),
                         (33, 7.2e9, "alpha", "030000")])
    body, pts = _points(c)
    # the agent API lists points newest first
    assert [v for v, _p in pts] == [7.2e9, 7.1e9], pts
    assert body["points"][0]["run_id"] == 33, "alpha's own run set it, not beta's"


def test_the_listing_says_the_run_was_left_out(tmp_path):
    c = _open(tmp_path, [(31, 7.1e9, "alpha", "010000"), (32, 7.2e9, "beta", "020000")])
    html = c.get("/state/versions?changes=all&limit=50").data.decode()
    assert html.count('<li class="state-version-row') == 1, "beta's run is not a version of alpha"
    assert "1 run whose saved chip identity does not match this chip" in html
    assert "of an uncertain chip identity" not in html, "one note for the left-out run, not two"


# ----------------------------------------------------------------------
# S10 final review P1-2 / P1-3: hide only a DECLARED other chip
# ----------------------------------------------------------------------

def _nameless_state(f01: float, qubits=("qA1",)) -> dict:
    return {"qubits": {q: {"id": q, "f_01": f01 if q == "qA1" else 5.0e9, "anharmonicity": 200e6}
                       for q in qubits},
            "qubit_pairs": {}, "active_qubit_names": list(qubits)}


def _open_nameless(tmp_path, runs, live_state):
    root = tmp_path / "data"
    for rid, state, hhmmss in runs:
        _seed_run(root, rid, state, hhmmss)
    live = tmp_path / "chips" / "a"
    _write_chip(live, live_state)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    c.post("/workspace/add", data={"folder": str(root)})
    declare_root(c, root)
    return c


def test_a_nameless_chip_that_gained_a_qubit_keeps_its_own_runs(tmp_path):
    """No declared name: the hardware fingerprint decided "uncertain" (a qubit was
    added), which proves no other chip -- the run stays, shown and never named as
    the writer, as before the lane rule."""
    two = ("qA1", "qA2")
    c = _open_nameless(tmp_path, [(31, _nameless_state(7.1e9), "010000"),
                                  (32, _nameless_state(7.2e9, two), "020000")],
                       _nameless_state(7.2e9, two))
    body, pts = _points(c)
    assert (7.2e9, "run_uncertain_chip") in pts, pts
    assert not body.get("other_chip_hidden")
    drawer = c.get("/field/history?path=qubits.qA1.f_01").data.decode()
    assert "#32 (chip uncertain)" in drawer and "not named as writer" in drawer
    assert 'data-note="other_chip"' not in drawer


def test_the_same_name_with_no_qubit_in_common_is_another_chip(tmp_path):
    other = {"qubits": {"qZ9": {"id": "qZ9", "f_01": 6.6e9}}, "qubit_pairs": {},
             "active_qubit_names": ["qZ9"], "extras": {"chip_name": "alpha"}}
    root = tmp_path / "data"
    _seed_run(root, 31, _state(7.1e9, "alpha"), "010000")
    _seed_run(root, 32, other, "020000")
    live = tmp_path / "chips" / "a"
    _write_chip(live, _state(7.1e9, "alpha"))
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    c.post("/workspace/add", data={"folder": str(root)})
    declare_root(c, root)
    body, _pts = _points(c)
    assert body.get("other_chip_hidden") == 1


def test_an_archived_chip_never_names_another_chips_run_as_its_writer(tmp_path):
    """The archived Param History reader has no folder view: a run of another
    chip in alpha's ledger is still read there, and it says so -- never "its own
    patch set it"."""
    from quam_state_manager.web import routes
    root = tmp_path / "data"
    for rid, f01, chip, hh in ((31, 7.1e9, "alpha", "010000"), (32, 7.2e9, "beta", "020000"),
                               (33, 7.3e9, "alpha", "030000")):
        _seed_run(root, rid, _state(f01, chip), hh)
        node = root / "2026-07-29" / f"#{rid}_08_qubit_spectroscopy_{hh}" / "node.json"
        body = json.loads(node.read_text(encoding="utf-8"))
        body["patches"] = [{"op": "replace", "path": "/qubits/qA1/f_01", "value": f01}]
        node.write_text(json.dumps(body), encoding="utf-8")
    live = tmp_path / "chips" / "a"
    _write_chip(live, _state(7.3e9, "alpha"))
    other = tmp_path / "chips" / "g"
    _write_chip(other, _state(5.0e9, "gamma"))
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    c.post("/workspace/add", data={"folder": str(root)})
    key = declare_root(c, root).name
    assert c.post("/load", data={"folder": str(other)}).status_code in (200, 302)
    html = c.get(f"/param-history/expand?chip_key={key}&qubit=qA1&prop=f_01").data.decode()
    assert "#32 (chip uncertain)" in html or "#32" not in html, "beta's run is never alpha's writer"
    assert "its own patch set it" not in html.split("#32")[1][:300] if "#32" in html else True
