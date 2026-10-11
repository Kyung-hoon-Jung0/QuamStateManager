"""Trends accuracy hard cases (v1.3.0 verification, VPLAN B.4), through the real app.

Each case builds a small synthetic lab (a live chip + a data folder of runs), opens it in the
real app and reads Chip Status > Trends (``/topology/trends``, the ``#topo-trends-data`` JSON)
or Datasets > Trends (``/trends/series``). The expected series is computed HERE from the
fixture's own saved states by an independent reference (``_expected``): runs in created_at
order, the value read through the state's own pointers, a change only where the value differs
under exact numeric equality (1 == 1.0, NaN == NaN); a point's key is the UTC second of the
run's created_at; a run is named as the writer only when its node.json patch set exactly that
leaf to exactly that value.
"""
from __future__ import annotations

import json
import math
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app
from tests.ledger_fixture import declare_root

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"},
           "ports": {"mw_outputs": {"con1": {"1": {"2": {"band": 1}}}}}}
_DAY = "2026-07-29"
_EXP = "08_qubit_spectroscopy"


def _qubit(f01=5.0e9, t1=1.0e-5, amp=0.30, alias="#./x180_DragCosine", amp2=0.40, cm=None):
    q = {"id": "qA1", "f_01": f01, "T1": t1, "anharmonicity": 200e6,
         "xy": {"operations": {"x180": alias,
                               "x180_DragCosine": {"amplitude": amp, "length": 40},
                               "x180_Square": {"amplitude": amp2, "length": 40}}}}
    if cm is not None:
        q["resonator"] = {"confusion_matrix": cm}
    return q


def _state(**kw) -> dict:
    return {"qubits": {"qA1": _qubit(**kw), "qA2": {"id": "qA2", "f_01": 6.0e9}}, "qubit_pairs": {},
            "active_qubit_names": ["qA1", "qA2"], "extras": {"chip_name": "alpha"}}


def _write_chip(folder: Path, state: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


def _run_dir(root: Path, rid: int, hhmmss: str) -> Path:
    return root / _DAY / f"#{rid}_{_EXP}_{hhmmss}"


def _seed(root: Path, rid: int, hhmmss: str, state: dict | None, patches=None,
          fit=None, targets=("qA1",)) -> Path:
    """One finished run: created_at is the folder clock at +09:00 (an aware instant)."""
    run = _run_dir(root, rid, hhmmss)
    run.mkdir(parents=True)
    created = f"{_DAY}T{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:]}+09:00"
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": _EXP, "status": "finished", "run_start": created, "run_end": created},
        "data": {"parameters": {"model": {"qubits": list(targets)}}, "outcomes": {}},
        "id": rid, "parents": [], "created_at": created, "patches": patches or [],
    }), encoding="utf-8")
    (run / "data.json").write_text(json.dumps({"fit_results": fit or {}}), encoding="utf-8")
    if state is not None:
        _write_chip(run / "quam_state", state)
    return run


def _open(tmp_path, runs, live_state):
    root = tmp_path / "data"
    for r in runs:
        _seed(root, *r[:3], **(r[3] if len(r) > 3 else {}))
    live = tmp_path / "chips" / "a"
    _write_chip(live, live_state)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    assert c.post("/workspace/add", data={"folder": str(root)}).status_code in (200, 302)
    declare_root(c, root)
    return c, root


def _charts(c, query: str) -> list[dict]:
    body = c.get("/topology/trends?" + query).get_data(as_text=True)
    m = re.search(r'id="topo-trends-data">(.*?)</script>', body, re.S)
    assert m, body[:400]
    return json.loads(m.group(1))


def _series(c, query: str, entity: str = "qA1") -> dict:
    for ch in _charts(c, query):
        for s in ch["series"]:
            if s["entity"].split(" · ")[0] == entity:
                return s
    return {"points": [], "held": {}, "attr": {}}


def _real(s: dict) -> list[tuple[str, object]]:
    """(UTC-second stamp, value) of every point that is not the held tail."""
    held = s.get("held") or {}
    return [(k.rsplit("_e", 1)[0], v) for k, v in s["points"] if k not in held]


# ------------------------------------------------------------------ the independent reference
def _same(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return (isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b)) or a == b
    return type(a) is type(b) and a == b


def _read(state: dict, path: str):
    """The value *path* names in *state*, following '#./' and '#/' pointers on the way."""
    cur: list[str] = []
    for seg in path.split("."):
        node = state
        for s in cur:
            node = node[s]
        v = node.get(seg) if isinstance(node, dict) else None
        if isinstance(v, str) and v.startswith("#./"):
            cur = cur + [v[3:]]
        elif isinstance(v, str) and v.startswith("#/"):
            cur = v[2:].split("/")
        else:
            cur = cur + [seg]
    node = state
    for s in cur:
        if not isinstance(node, dict) or s not in node:
            return None
        node = node[s]
    return node


def _stamp(hhmmss: str) -> str:
    local = datetime.fromisoformat(f"{_DAY}T{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:]}+09:00")
    return local.astimezone(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _expected(runs, path: str) -> list[tuple[str, object]]:
    """Change points of *path* over the runs that saved a state, in created_at order; a
    non-finite number is drawn as a gap (None)."""
    out, prev, have = [], None, False
    for r in sorted((r for r in runs if r[2] is not None), key=lambda r: r[1]):
        v = _read(r[2], path)
        if have and _same(prev, v):
            continue
        prev, have = v, True
        out.append((_stamp(r[1]), v if isinstance(v, (int, float)) and math.isfinite(v) else None))
    return out


# ------------------------------------------------------------------ cases
def test_a_point_sits_at_the_runs_utc_second_not_its_local_clock(tmp_path):
    runs = [(1, "100000", _state(f01=5.0e9)), (2, "110000", _state(f01=5.1e9))]
    c, _root = _open(tmp_path, runs, _state(f01=5.1e9))
    got = _real(_series(c, "metrics=f_01"))
    assert got == _expected(runs, "qubits.qA1.f_01")
    assert got[0][0] == "20260729_010000", "10:00 at +09:00 is 01:00 UTC (never 9 h off)"


def test_the_same_value_resaved_and_an_int_float_resave_are_no_change(tmp_path):
    runs = [(1, "100000", _state(f01=5000000000)), (2, "101000", _state(f01=5000000000.0)),
            (3, "102000", _state(f01=5000000000.0)), (4, "103000", _state(f01=5.2e9))]
    c, _root = _open(tmp_path, runs, _state(f01=5.2e9))
    got = _real(_series(c, "metrics=f_01"))
    assert got == _expected(runs, "qubits.qA1.f_01") == [
        ("20260729_010000", 5000000000), ("20260729_013000", 5.2e9)]


def test_nan_is_a_gap_and_nan_resaved_is_no_change(tmp_path):
    nan = float("nan")
    runs = [(1, "100000", _state(t1=1.0e-5)), (2, "101000", _state(t1=nan)),
            (3, "102000", _state(t1=nan)), (4, "103000", _state(t1=2.0e-5))]
    c, _root = _open(tmp_path, runs, _state(t1=2.0e-5))
    got = _real(_series(c, "metrics=T1"))
    assert got == _expected(runs, "qubits.qA1.T1") == [
        ("20260729_010000", 1.0e-5), ("20260729_011000", None), ("20260729_013000", 2.0e-5)]


def test_an_alias_retarget_is_a_change_of_the_value_read_through_it(tmp_path):
    runs = [(1, "100000", _state()), (2, "101000", _state(alias="#./x180_Square")),
            (3, "102000", _state(alias="#./x180_Square", amp2=0.45))]
    c, _root = _open(tmp_path, runs, runs[-1][2])
    path = "qubits.qA1.xy.operations.x180.amplitude"
    got = _real(_series(c, "metrics=&paths=" + path))
    assert got == _expected(runs, path) == [
        ("20260729_010000", 0.30), ("20260729_011000", 0.40), ("20260729_012000", 0.45)]


def test_a_run_is_named_as_writer_only_with_its_own_patch(tmp_path):
    def patch(v):
        return [{"op": "replace", "path": "/quam/qubits/qA1/f_01", "value": v}]
    runs = [(1, "100000", _state(f01=5.0e9)),
            (2, "101000", _state(f01=5.1e9), {"patches": patch(5.1e9)}),
            (3, "102000", _state(f01=5.2e9), {"patches": patch(5.25e9)})]
    c, _root = _open(tmp_path, runs, _state(f01=5.2e9))
    s = _series(c, "metrics=f_01")
    by_stamp = {k.rsplit("_e", 1)[0]: a for k, a in (s.get("attr") or {}).items()}
    proven = by_stamp["20260729_011000"]
    assert proven["provenance"] == "run_proven" and proven["uid"].endswith(":2")
    unproven = by_stamp["20260729_012000"]
    assert unproven["provenance"] != "run_proven" and "uid" not in unproven
    assert unproven.get("saved_uid", ":3").endswith(":3")


def test_a_run_without_a_saved_state_is_not_a_point(tmp_path):
    runs = [(1, "100000", _state(f01=5.0e9)), (2, "101000", None), (3, "102000", _state(f01=5.2e9))]
    c, _root = _open(tmp_path, runs, _state(f01=5.2e9))
    got = _real(_series(c, "metrics=f_01"))
    assert got == _expected(runs, "qubits.qA1.f_01") == [
        ("20260729_010000", 5.0e9), ("20260729_012000", 5.2e9)]


def test_a_run_copied_late_lands_at_its_instant_and_its_successor_is_rediffed(tmp_path):
    late = (2, "101000", _state(f01=5.2e9))
    runs = [(1, "100000", _state(f01=5.0e9)), (3, "102000", _state(f01=5.2e9))]
    c, root = _open(tmp_path, runs, _state(f01=5.2e9))
    assert _real(_series(c, "metrics=f_01")) == _expected(runs, "qubits.qA1.f_01")
    _seed(root, *late)
    declare_root(c, root)
    got = _real(_series(c, "metrics=f_01"))
    assert got == _expected(runs + [late], "qubits.qA1.f_01") == [
        ("20260729_010000", 5.0e9), ("20260729_011000", 5.2e9)], \
        "the late run carries the change; the run after it re-saved the same value"


def test_datasets_trends_is_each_complete_runs_fit_result_in_run_id_order(tmp_path):
    nan = float("nan")
    runs = [(1, "100000", _state(), {"fit": {"qA1": {"f_01": 5.0e9, "success": True}}}),
            (2, "101000", _state(), {"fit": {"qA1": {"f_01": nan, "success": False}}}),
            (3, "102000", _state(), {"fit": {"qA1": {"f_01": 5.2e9, "success": True}}})]
    c, _root = _open(tmp_path, runs, _state())
    r = c.get(f"/trends/series?experiment={_EXP}")
    assert r.status_code == 200
    js = json.loads(r.get_data())
    assert [x[0] for x in js["runs"]] == [1, 2, 3]
    for (rid, t_ms, uid), (_rid, hhmmss, *_r) in zip(js["runs"], runs):
        want = datetime.fromisoformat(f"{_DAY}T{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:]}+09:00")
        assert t_ms == int(want.timestamp() * 1000), "x is the run's created_at instant"
        assert uid.endswith(f":{rid}")
    series = {(s["q"], s["m"]): s["v"] for s in js["series"]}
    assert series[("qA1", "f_01")] == [5.0e9, None, 5.2e9], "a non-finite fit is a null, not dropped"
    assert ("qA1", "success") not in series
    # the click opens the same run
    page = c.get("/dataset/" + js["runs"][2][2], headers={"HX-Request": "true"}).get_data(as_text=True)
    assert f'data-experiment="{_EXP}"' in page and 'data-run-id="3"' in page
    assert f'data-date="{_DAY}"' in page


def _at(points: list[tuple[str, object]], stamp: str):
    """The value a step chart shows at UTC stamp *stamp* (points as from ``_real``)."""
    v = None
    for k, val in points:
        if k <= stamp:
            v = val
    return v


# ------------------------------------------------------------------ findings (WP3), fixed in v1.3.0
# v1.3.0: strict xfail -> pass, the four findings are fixed (FIX-TR, ADJ_TR); each pin is mutation-checked
def test_tr01_a_chain_of_contradicted_changes_ends_on_the_recorded_value(tmp_path):
    """qA1 runs save A then B; two runs that did not measure qA1 then save A and B (two lines of runs
    flipping the value); two more agree on B. Every state recorded after run 4 holds B, so B is in
    force from run 4 on -- the witness rule's own reading of a run that did not measure the qubit."""
    other = {"targets": ("qA2",)}
    runs = [(1, "100000", _state(f01=5.00e9)), (2, "101000", _state(f01=5.10e9)),
            (3, "102000", _state(f01=5.00e9), other), (4, "103000", _state(f01=5.10e9), other),
            (5, "104000", _state(f01=5.10e9), other), (6, "105000", _state(f01=5.10e9), other),
            (7, "110000", _state(f01=5.20e9))]
    c, _root = _open(tmp_path, runs, _state(f01=5.20e9))
    got = _real(_series(c, "metrics=f_01"))
    assert _at(got, _stamp("104500")) == 5.10e9, got


def test_tr02a_a_derived_fidelity_point_is_the_fidelity_of_that_runs_matrix(tmp_path):
    other = {"targets": ("qA2",)}
    cm1, cm2 = [[0.98, 0.02], [0.06, 0.94]], [[0.99, 0.01], [0.04, 0.96]]
    cm3, cm4 = [[0.99, 0.01], [0.06, 0.94]], [[0.97, 0.03], [0.05, 0.95]]
    runs = [(1, "100000", _state(cm=cm1)), (2, "101000", _state(cm=cm2)),
            (3, "102000", _state(cm=cm3), other), (4, "103000", _state(cm=cm4))]
    c, _root = _open(tmp_path, runs, _state(cm=cm4))
    got = dict(_real(_series(c, "metrics=assignment_fidelity")))
    at2 = got.get(_stamp("101000"))
    assert at2 is None or abs(at2 - (0.99 + 0.96) / 2) <= 1e-12, (
        f"run 2 saved fidelity 0.975; the point at run 2 shows {at2}", got)


def test_tr03_a_copied_data_folder_keeps_the_run_links(tmp_path):
    def patch(v):
        return [{"op": "replace", "path": "/quam/qubits/qA1/f_01", "value": v}]
    runs = [(1, "100000", _state(f01=5.0e9)),
            (2, "101000", _state(f01=5.1e9), {"patches": patch(5.1e9)})]
    c, root = _open(tmp_path, runs, _state(f01=5.1e9))

    def uid_at_run2():
        attrs = _series(c, "metrics=f_01").get("attr") or {}
        return next((a.get("uid") for k, a in attrs.items() if k.startswith(_stamp("101000"))), None)
    assert (uid_at_run2() or "").endswith(":2"), "precondition: linked before the move"
    # the lab copies its data folder to a new place and works from the copy; the old one stays on disk
    moved = tmp_path / "data_copy"
    shutil.copytree(str(root), str(moved))
    assert c.post("/workspace/remove", data={"folder": str(root)}).status_code in (200, 302)
    assert c.post("/workspace/add", data={"folder": str(moved)}).status_code in (200, 302)
    declare_root(c, moved)
    assert (uid_at_run2() or "").endswith(":2"), "the proven point still opens its run after the move"


def test_tr04_the_report_trends_caption_names_no_stale_cap(tmp_path):
    runs = [(1, "100000", _state(f01=5.0e9)), (2, "101000", _state(f01=5.1e9))]
    c, _root = _open(tmp_path, runs, _state(f01=5.1e9))
    body = c.get("/chip-status/report/section/trends?window=all").get_data(as_text=True)
    assert "rep-cap" in body
    assert "400 per line" not in re.sub(r"\s+", " ", body)


# ------------------------------------------------------------------ v1.3.0 (FIX-TR): the fold and the composites
def _ledger_series(root_tmp, path: str) -> list[tuple[int, object]]:
    """(run id, value) of every row of holder *path* in the chip's own ledger series."""
    from types import SimpleNamespace
    from quam_state_manager.core import hub_query
    found = sorted((root_tmp / "_inst" / "history").glob("*/ledger.sqlite"))
    assert len(found) == 1, found
    rows = hub_query.series(SimpleNamespace(directory=found[0].parent), path)
    return [(r[0].get("run_id"), r[2]) for r in rows]


def _poll_live(c) -> None:
    """Read the live files once (what the sync poll does): the chip's value now is then known."""
    from quam_state_manager.web import routes
    with c.application.test_request_context():
        routes._sync_live_refresh(routes._active_ctx())


_DOUBT = ("no later run has read the chip yet", "changed again before the chip was read",
          "the next read of the chip found another value", "not in the next recorded state")


def _attr_at(s: dict, stamp: str) -> dict:
    return next((a for k, a in (s.get("attr") or {}).items() if k.startswith(stamp)), {})


def _doubt(attr: dict) -> str | None:
    return next((w for w in _DOUBT if w in (attr.get("sub") or "")), None)


def test_a_restoring_row_that_carries_no_change_still_leaves(tmp_path):
    """TR-01's fold keeps the shipped rule where the restore changes nothing: run 2 (qA1) saves B,
    run 3 (another qubit) still has A and so does run 4 -- the pair leaves, and run 3's own row
    (B -> A against a series that never left A) leaves with it."""
    other = {"targets": ("qA2",)}
    runs = [(1, "100000", _state(f01=5.00e9)), (2, "101000", _state(f01=5.10e9)),
            (3, "102000", _state(f01=5.00e9), other), (4, "103000", _state(f01=5.00e9), other)]
    c, _root = _open(tmp_path, runs, _state(f01=5.00e9))
    got = _real(_series(c, "metrics=f_01"))
    assert got == [(_stamp("100000"), 5.00e9)], got
    assert _ledger_series(tmp_path, "qubits.qA1.f_01") == [(1, 5.00e9)], \
        "run 3 did not change the value: its restoring row is not a change of the chip's series"


@pytest.mark.parametrize("live", [5.10e9, 5.00e9])
def test_the_newest_restoring_row_is_settled_by_the_live_value(tmp_path, live):
    """A chain A (qA1) -> B (qA1) -> A (qA2) -> B (qA2, newest): run 4's row stays (it puts back
    B, which the series no longer holds) and, with no later read, the chip's value now decides
    it -- the same live tail as any newest change."""
    other = {"targets": ("qA2",)}
    runs = [(1, "100000", _state(f01=5.00e9)), (2, "101000", _state(f01=5.10e9)),
            (3, "102000", _state(f01=5.00e9), other), (4, "103000", _state(f01=5.10e9), other)]
    c, _root = _open(tmp_path, runs, _state(f01=live))
    _poll_live(c)
    s = _series(c, "metrics=f_01")
    got = _real(s)
    if live == 5.10e9:
        assert got == [(_stamp("100000"), 5.00e9), (_stamp("103000"), 5.10e9)], got
        assert _doubt(_attr_at(s, _stamp("103000"))) is None, "the chip holds it: a plain point"
    else:
        assert got == [(_stamp("100000"), 5.00e9)], got
        html = c.get("/field/history", query_string={"path": "qubits.qA1.f_01"}).get_data(as_text=True)
        nk = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.split('<details class="vh-not-kept">', 1)[1]
                                         .split("</details>", 1)[0]))
        assert "Saved by run #4" in nk and "(the chip holds" in nk, nk


def _ptr_state(alias: str, sq: float, other_pulse: float = 0.35) -> dict:
    st = _state(alias=alias, amp2=sq)
    st["qubits"]["qA1"]["xy"]["operations"]["x180_Gauss"] = {"amplitude": other_pulse, "length": 40}
    return st


def test_tr02b_a_pointer_followed_point_is_the_value_the_run_saved_through_the_pointer(tmp_path):
    """x180 points at DragCosine (0.30); run 2 (qA1) moves it to Square and saves Square 0.45;
    run 3 (qA1) keeps Square at 0.40; run 4 (another qubit) confirms. Square's own 0.45 came
    back exactly to its anchor (an excursion of the holder), but the value IN FORCE through the
    path at run 2 is the 0.45 run 2 saved through the pointer -- shown, labelled -- never a value
    of the holder's filtered series (0.40) no run saved through the path then."""
    path = "qubits.qA1.xy.operations.x180.amplitude"
    other = {"targets": ("qA2",)}
    runs = [(1, "100000", _ptr_state("#./x180_DragCosine", 0.40)),
            (2, "101000", _ptr_state("#./x180_Square", 0.45)),
            (3, "102000", _ptr_state("#./x180_Square", 0.40)),
            (4, "103000", _ptr_state("#./x180_Square", 0.40), other)]
    c, _root = _open(tmp_path, runs, runs[-1][2])
    s = _series(c, "metrics=&paths=" + path)
    got = _real(s)
    assert got == _expected(runs, path) == [
        (_stamp("100000"), 0.30), (_stamp("101000"), 0.45), (_stamp("102000"), 0.40)], got
    assert _doubt(_attr_at(s, _stamp("101000"))) == "changed again before the chip was read"
    assert _doubt(_attr_at(s, _stamp("102000"))) is None


_CM1, _CM2 = [[0.98, 0.02], [0.06, 0.94]], [[0.99, 0.01], [0.04, 0.96]]


def _composite_answer(c, q: str = "qA1", prop: str = "assignment_fidelity") -> dict:
    """The derived metric's own answer (``value_history.composite_target`` through the
    Chip Status table): its in-force series and what it left out."""
    from quam_state_manager.core.history import _DERIVED_FIDELITY_PROPS
    from quam_state_manager.web import hub_status, routes
    with c.application.test_request_context():
        _ans, table = routes._hub_status_table(routes._active_ctx())
        base, paths = table._matrix_paths(q, _DERIVED_FIDELITY_PROPS[prop][0])
        key = hub_status._derived_key(base, prop)
        comp = {key: (base, tuple(paths), hub_status._fidelity_of(_DERIVED_FIDELITY_PROPS[prop][1], base, paths),
                      prop)}
        return table.series_many([], comp)["rows"][key]


def test_a_composite_whose_whole_matrix_came_back_is_one_excursion(tmp_path):
    """cm1; run 2 (qA1) saves cm2; run 3 (qA1) saves cm1 again, which run 4 (another qubit)
    confirms: the matrix came back exactly, so runs 2-3 are one excursion of the composite --
    no point, listed apart."""
    other = {"targets": ("qA2",)}
    runs = [(1, "100000", _state(cm=_CM1)), (2, "101000", _state(cm=_CM2)),
            (3, "102000", _state(cm=_CM1)), (4, "103000", _state(cm=_CM1), other)]
    c, _root = _open(tmp_path, runs, _state(cm=_CM1))
    got = _real(_series(c, "metrics=assignment_fidelity"))
    assert [k for k, _v in got] == [_stamp("100000")] and abs(got[0][1] - 0.96) <= 1e-12, got
    row = _composite_answer(c)
    assert [[p["run_id"] for p in e["points"]] + [e["back"]["run_id"]] for e in row["excursions"]] == [[2, 3]]
    assert row["not_kept"] == []


def test_a_composite_restored_as_a_unit_leaves_with_its_restoring_row(tmp_path):
    """cm1; run 2 (qA1) saves cm2; run 3 (another qubit) still has cm1: the composite's change is
    contradicted, it and run 3's restoring row leave, and the change is listed -- never silent."""
    other = {"targets": ("qA2",)}
    runs = [(1, "100000", _state(cm=_CM1)), (2, "101000", _state(cm=_CM2)),
            (3, "102000", _state(cm=_CM1), other)]
    c, _root = _open(tmp_path, runs, _state(cm=_CM1))
    got = _real(_series(c, "metrics=assignment_fidelity"))
    assert [k for k, _v in got] == [_stamp("100000")], got
    row = _composite_answer(c)
    nk = row["not_kept"]
    assert [(n["run_id"], n["by"]["run_id"]) for n in nk] == [(2, 3)], nk
    assert abs(nk[0]["value"] - 0.975) <= 1e-12 and abs(nk[0]["by"]["value"] - 0.96) <= 1e-12


def test_a_kept_composite_is_judged_again_when_a_later_read_lands(tmp_path):
    """A composite's verdicts live in its answer, and an answer is kept between reads (docs/298).
    Run 4 measures another qubit and saves nothing of qA1's x180: it is no row of the composite,
    yet it is the read that confirms run 3. The kept answer is served again only while the same
    rows are judged the same -- here they are not, so run 3's point loses its doubt."""
    path = "qubits.qA1.xy.operations.x180.amplitude"
    runs = [(1, "100000", _ptr_state("#./x180_DragCosine", 0.40)),
            (2, "101000", _ptr_state("#./x180_Square", 0.45)),
            (3, "102000", _ptr_state("#./x180_Square", 0.40))]
    c, root = _open(tmp_path, runs, runs[-1][2])
    s = _series(c, "metrics=&paths=" + path)
    assert _doubt(_attr_at(s, _stamp("102000"))) == "no later run has read the chip yet"
    later = _ptr_state("#./x180_Square", 0.40)
    later["qubits"]["qA2"]["f_01"] = 6.1e9
    _seed(root, 4, "103000", later, targets=("qA2",))
    declare_root(c, root)
    s = _series(c, "metrics=&paths=" + path)
    assert _real(s) == _expected(runs, path), _real(s)
    assert _doubt(_attr_at(s, _stamp("102000"))) is None, "run 4 read the chip: run 3 is confirmed"
    assert _doubt(_attr_at(s, _stamp("101000"))) == "changed again before the chip was read"
