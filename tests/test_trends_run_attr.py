"""Every surface that maps a value to a run names the run that WROTE it.

Customer report 2026-09-29 (KRISS_CZ): *"one IRB point says its run is a flux
short distortion experiment -- how can that be? T1 points also point to runs
that are not T1."* And, on the click: *"wrong information is the worst."* A
history snapshot names the run whose SAVE it copied; that save carries every
value written before it. So a surface names that run only when
``core.value_writer`` shows it wrote the value, names the real writer when one
is found among the runs before it, and otherwise says "captured with run #N
(not the run that measured it)" -- and never OPENS a run that did not write
the value.

The fixture is one chip, one dataset root and four snapshots:

  S0  manual                       T1 = 2.0e-5
  S1  experiment #401 24_all_xy    T1 = 2.5e-5   written by #394 25_T1 (uncaptured)
  S2  experiment #403 15b_readout  T1 = 3.3e-5   written by an edit, first saved
                                                 by #402 04d_twpa -> captured only
  S3  experiment #404 25_T1        T1 = 4.0e-5   #404 itself (patches)
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quam_state_manager.core import value_writer as vw
from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "3.3.3.3", "cluster_name": "C9"},
           "ports": {"mw_outputs": {"con1": {"1": {"2": {"band": 1}}}}}}


def _state(t1: float) -> dict:
    return {"qubits": {"qA1": {"id": "qA1", "f_01": 6.0e9, "T1": t1}},
            "qubit_pairs": {}, "active_qubit_names": ["qA1"]}


def _write_chip(folder: Path, state: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


def _run(root: Path, rid: int, name: str, t1: float, *, patches=None,
         outcomes=None) -> Path:
    f = root / "2026-09-01" / f"#{rid}_{name}_0100{rid % 100:02d}"
    _write_chip(f / "quam_state", _state(t1))
    node = {"metadata": {"name": name, "status": "finished",
                         "run_start": datetime.now(timezone.utc).isoformat(),
                         "run_end": datetime.now(timezone.utc).isoformat()},
            "data": {"parameters": {"model": {"qubits": ["qA1"]}},
                     "outcomes": outcomes or {"qA1": "successful"}},
            "id": rid, "parents": []}
    if patches is not None:
        node["patches"] = patches
    (f / "node.json").write_text(json.dumps(node), encoding="utf-8")
    (f / "data.json").write_text("{}", encoding="utf-8")
    return f


@pytest.fixture
def env(tmp_path, monkeypatch):
    vw.clear_caches()
    # an inline answer: the budgeted background path has its own test below
    monkeypatch.setattr(routes_mod, "_WRITER_BUDGET_S", 120.0)
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state(2.0e-5))
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    data = tmp_path / "data"
    runs = {
        390: _run(data, 390, "11_power_rabi", 2.0e-5),
        394: _run(data, 394, "25_T1", 2.5e-5),
        401: _run(data, 401, "24_all_xy", 2.5e-5),
        402: _run(data, 402, "04d_twpa_fine_tuning", 3.3e-5),
        403: _run(data, 403, "15b_readout_weights_optimization", 3.3e-5),
        404: _run(data, 404, "25_T1", 4.0e-5, patches=[
            {"op": "replace", "path": "/quam/qubits/qA1/T1", "value": 4.0e-5}]),
    }
    c.post("/workspace/add", data={"folder": str(data)})
    hm = app.config["history_manager"]

    def snap(t1, trigger="manual", run=None, name=None):
        _write_chip(live, _state(t1))
        kw = {}
        if run is not None:
            kw = {"run_id": run, "experiment_name": name,
                  "experiment_folder_path": str(runs[run])}
        m = hm.check_and_snapshot(str(live), trigger, force=True, **kw)
        assert m is not None
        return m.timestamp
    yield {"client": c, "snap": snap, "runs": runs, "hm": hm, "live": live}
    vw.clear_caches()


def _four(env):
    s = env["snap"]
    return (s(2.0e-5), s(2.5e-5, "experiment", 401, "24_all_xy"),
            s(3.3e-5, "experiment", 403, "15b_readout_weights_optimization"),
            s(4.0e-5, "experiment", 404, "25_T1"))


def _charts(body: str) -> list[dict]:
    m = re.search(r'id="topo-trends-data">(.*?)</script>', body, re.S)
    return json.loads(m.group(1)) if m else []


class TestTrends:
    def test_each_point_names_its_writer_or_says_captured(self, env):
        s0, s1, s2, s3 = _four(env)
        body = env["client"].get("/topology/trends?metrics=T1").get_data(as_text=True)
        (chart,) = [c for c in _charts(body) if c["metric"] == "T1"]
        (ser,) = chart["series"]
        attr = ser.get("attr") or {}
        # S1: the AllXY run only carried it -- #394 25_T1 wrote it, and the
        # point opens #394, never #401
        assert attr[s1]["run"] == 394 and attr[s1]["short"] == "25 T1"
        assert attr[s1]["uid"] and attr[s1]["uid"].endswith(":394")
        # S2: no run of the family wrote it -> captured, no uid to open
        assert attr[s2] == {"captured": True}
        # S3: the captured run wrote it -> the snapshot's own answer stands
        assert s3 not in attr and s0 not in attr

    def test_the_snapshot_map_still_names_the_capturer(self, env):
        """The override rides the SERIES; the per-snapshot map is unchanged,
        so the client can say "captured later with #401" beside the writer."""
        _, s1, _, _ = _four(env)
        body = env["client"].get("/topology/trends?metrics=T1").get_data(as_text=True)
        m = re.search(r'id="topo-trends-snaps">(.*?)</script>', body, re.S)
        snaps = json.loads(m.group(1))
        assert snaps[s1]["run"] == 401


class TestChipStatusMetricMeta:
    def test_last_changed_by_names_the_writer(self, env):
        s = env["snap"]
        s(2.0e-5)
        ts = s(2.5e-5, "experiment", 401, "24_all_xy")
        d = env["client"].get("/topology/metric-meta").get_json()
        e = d["q"]["T1"]["qA1"]
        assert e["ts"] == ts
        assert e["writer"]["run"] == 394 and e["writer"]["short"] == "25 T1"

    def test_a_captured_only_value_says_so(self, env):
        s = env["snap"]
        s(2.0e-5)
        s(2.5e-5, "experiment", 401, "24_all_xy")
        s(3.3e-5, "experiment", 403, "15b_readout_weights_optimization")
        d = env["client"].get("/topology/metric-meta").get_json()
        assert d["q"]["T1"]["qA1"]["writer"] == {"captured": True}


class TestValueHistoryDrawer:
    def test_the_data_button_opens_only_the_writer(self, env):
        _four(env)
        html = env["client"].get("/field/history?path=qubits.qA1.T1").get_data(as_text=True)
        # the carried value: named and opened as #394, not #401
        assert re.search(r'#394 25_T1', html)
        assert '/dataset/' in html and ':394"' in html
        assert ':401"' not in html, "a Data button opened the run that only carried it"
        # the edit-carried value: captured, no Data for #403
        assert "captured with #403" in html and "not the run that measured it" in html
        assert ':403"' not in html


class TestParamHistoryDrawer:
    def test_only_a_writer_is_clickable(self, env):
        s0, s1, s2, s3 = _four(env)
        env["snap"](4.0e-5, "experiment", 401, "24_all_xy")   # unchanged value
        html = env["client"].get("/param-history/expand?qubit=qA1&prop=T1").get_data(as_text=True)
        m = re.search(r'id="phd-data" type="application/json">(.*?)</script>', html, re.S)
        row = json.loads(m.group(1))
        by_ts = {p["timestamp"]: p for p in row["values"]}
        assert by_ts[s1]["writer"]["run"] == 394 and by_ts[s1]["uid"].endswith(":394")
        assert by_ts[s2]["writer"] == {"captured": True} and by_ts[s2]["uid"] is None
        assert "writer" not in by_ts[s3] and by_ts[s3]["uid"].endswith(":404")
        last = max(by_ts)
        assert by_ts[last].get("unchanged") and by_ts[last]["uid"] is None


class TestAColdArchiveNeverBlocksNorGuesses:
    """A cold first answer parses a run's 1.6 MB state per check (5.6 s for
    the default Trends open on the real KRISS_CZ chip). Past the budget the
    rest is checked in the background: those points are PENDING -- no run
    named, nothing opened -- the section carries the re-fetch note, the
    partial fragment is not memoised, and the re-fetch has the answers."""

    def test_pending_then_answered(self, env, monkeypatch):
        import time
        _, s1, s2, _ = _four(env)
        monkeypatch.setattr(routes_mod, "_WRITER_BUDGET_S", 0.0)
        body = env["client"].get("/topology/trends?metrics=T1").get_data(as_text=True)
        (ser,) = [c for c in _charts(body) if c["metric"] == "T1"][0]["series"]
        assert ser["attr"][s1] == {"pending": True}
        assert ser["attr"][s2] == {"pending": True}
        assert "Checking which run wrote each point" in body
        assert 'data-trends-updating="1"' in body
        deadline = time.monotonic() + 30
        while True:
            body = env["client"].get("/topology/trends?metrics=T1").get_data(as_text=True)
            (ser,) = [c for c in _charts(body) if c["metric"] == "T1"][0]["series"]
            if not any(a.get("pending") for a in (ser.get("attr") or {}).values()):
                break
            assert time.monotonic() < deadline, "the background check never finished"
            time.sleep(0.2)
        assert ser["attr"][s1]["run"] == 394 and ser["attr"][s2] == {"captured": True}
        assert "Checking which run wrote each point" not in body

    def test_metric_meta_says_updating_while_pending(self, env, monkeypatch):
        s = env["snap"]
        s(2.0e-5)
        s(2.5e-5, "experiment", 401, "24_all_xy")
        monkeypatch.setattr(routes_mod, "_WRITER_BUDGET_S", 0.0)
        d = env["client"].get("/topology/metric-meta").get_json()
        assert d["q"]["T1"]["qA1"]["writer"] == {"pending": True} and d["updating"] is True
