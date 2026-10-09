"""Every surface that maps a value to a run names the run that WROTE it.

Customer report 2026-09-29 (lab-F-env): *"one IRB point says its run is a flux
short distortion experiment -- how can that be? T1 points also point to runs
that are not T1."* And, on the click: *"wrong information is the worst."* A
history snapshot names the run whose SAVE it copied; that save carries every
value written before it. So a surface names a run as the writer only when the
chip's change ledger proves it (the run's own patch set the value), and
otherwise says what the ledger knows ("saved in #N, writer not proven", an
observed state with its writer unknown) -- and never OPENS a run as the writer
when it did not write the value. (S10 C5: the snapshot writer check is no longer read by
these surfaces.)

The fixture is one chip, one dataset root and four snapshots:

  S0  manual                       T1 = 2.0e-5
  S1  experiment #401 24_all_xy    T1 = 2.5e-5   written by #394 25_T1 (uncaptured)
  S2  experiment #403 15b_readout  T1 = 3.3e-5   written by an edit, first saved
                                                 by #402 04d_twpa -> captured only
  S3  experiment #404 25_T1        T1 = 4.0e-5   #404 itself (patches)
"""
from __future__ import annotations

# S10 C7: old -> new, remove callerless snapshot hooks and retain ledger behavior.

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app
from tests.ledger_fixture import declare_root

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
    # S10 C5: retired writer caches + the writer-check budget -> not read, these surfaces read the ledger.
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
    # S10 C3: workspace writer scans -> declared ledger fixture, provenance requires an event's patch.
    declare_root(c, data)
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


def _four(env):
    # S10 C3: fake starting capturer -> the real starting run, ledger chronology supplies provenance.
    s = env["snap"]
    return (s(2.0e-5, "experiment", 390, "11_power_rabi"), s(2.5e-5, "experiment", 401, "24_all_xy"),
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
        # S10 C3: inferred writer by run family -> patch proof, saved states stay explicitly unproven.
        proven = [a for a in attr.values() if a["provenance"] == "run_proven"]
        saved = [a for a in attr.values() if a["provenance"] == "run_saved"]
        assert len(proven) == 1 and proven[0]["uid"].endswith(":404")
        assert saved and any(a.get("saved_uid", "").endswith(":394") for a in saved)
        assert all("uid" not in a for a in saved)

    # S10 C5: renamed from ..._snapshot_map_still_names_the_capturer -- the map is empty now
    def test_the_snapshot_map_is_empty_and_each_point_carries_its_words(self, env):
        """No per-snapshot map: every point carries the ledger's own words in
        its series' attr, and a run that only saved the value is never its writer."""
        _, s1, _, _ = _four(env)
        body = env["client"].get("/topology/trends?metrics=T1").get_data(as_text=True)
        m = re.search(r'id="topo-trends-' r'snaps">(.*?)</script>', body, re.S)
        snaps = json.loads(m.group(1)) if m else {}
        # S10 C3: snapshot capturer map -> ledger point context, carried saves invent no changes.
        assert snaps == {}
        (ser,) = [c for c in _charts(body) if c["metric"] == "T1"][0]["series"]
        attrs = ser["attr"]
        assert all("provenance" in a and "label" in a for a in attrs.values())
        assert any(a.get("uid", "").endswith(":404") for a in attrs.values())
        assert all(not a.get("uid", "").endswith(":401") for a in attrs.values())


class TestChipStatusMetricMeta:
    def test_last_changed_by_names_the_writer(self, env):
        s = env["snap"]
        _four(env)
        d = env["client"].get("/topology/metric-meta").get_json()
        e = d["q"]["T1"]["qA1"]
        # S10 C3: inferred snapshot writer -> newest proven ledger write, with the event's instant.
        assert e["provenance"] == "run_proven" and e["run"] == 404
        assert e["writer"]["run"] == 404 and e["writer"]["uid"].endswith(":404")

    def test_a_captured_only_value_says_so(self, env):
        s = env["snap"]
        s(2.0e-5)
        s(2.5e-5, "experiment", 401, "24_all_xy")
        s(3.3e-5, "experiment", 403, "15b_readout_weights_optimization")
        d = env["client"].get("/topology/metric-meta").get_json()
        # S10 C3: captured-only snapshot hint -> observed event, never invent a measuring run.
        entry = d["q"]["T1"]["qA1"]
        assert entry["provenance"] == "observed" and entry["run"] is None
        assert entry.get("uid") is None and "writer unknown" in entry["sub"]


class TestValueHistoryDrawer:
    def test_the_data_button_opens_only_the_writer(self, env):
        _four(env)
        html = env["client"].get("/field/history?path=qubits.qA1.T1").get_data(as_text=True)
        # S10 C3: inferred writer links -> proven ledger writer, saved-run links say unproven.
        assert "run #404" in html and ':404"' in html
        assert "not proven" in html and ':394?via=saved"' in html
        assert ':401"' not in html and ':403"' not in html


class TestParamHistoryDrawer:
    def test_only_a_writer_is_clickable(self, env):
        s0, s1, s2, s3 = _four(env)
        env["snap"](4.0e-5, "experiment", 401, "24_all_xy")   # unchanged value
        html = env["client"].get("/param-history/expand?qubit=qA1&prop=T1").get_data(as_text=True)
        m = re.search(r'id="phd-data" type="application/json">(.*?)</script>', html, re.S)
        row = json.loads(m.group(1))
        # S10 C3: snapshot customdata -> shared ledger context, only own-patch proof names a writer.
        attrs = row["values"]
        proven = [a for a in attrs if a["provenance"] == "run_proven"]
        assert len(proven) == 1 and proven[0]["uid"].endswith(":404")
        assert all(a["uid"] is None for a in attrs if a["provenance"] != "run_proven")
        assert all(a["run"] not in (401, 403) for a in attrs)


class TestAColdArchiveNeverBlocksNorGuesses:
    """A cold first answer parses a run's 1.6 MB state per check (5.6 s for
    the default Trends open on the real lab-F-env chip). Past the budget the
    rest is checked in the background: those points are PENDING -- no run
    named, nothing opened -- the section carries the re-fetch note, the
    partial fragment is not memoised, and the re-fetch has the answers."""

    def test_pending_then_answered(self, env, monkeypatch):
        _, s1, s2, _ = _four(env)
        # S10 C3: snapshot writer budget -> ledger preparation wait, no guessed rows before readiness.
        from quam_state_manager.core import value_history, ramcache
        real = value_history.read
        def preparing(*a, **k):
            raise ramcache.Warming("fixture", "key", 0)
        monkeypatch.setattr(value_history, "read", preparing)
        body = env["client"].get("/topology/trends?metrics=T1").get_data(as_text=True)
        # S10 C5 (C3 review): 'load delay:' (a self-fetching note) -> the client's re-ask marker
        assert 'data-vh-mode="preparing"' in body and 'data-trends-updating="1"' in body
        assert _charts(body) == []
        monkeypatch.setattr(value_history, "read", real)
        body = env["client"].get("/topology/trends?metrics=T1").get_data(as_text=True)
        (ser,) = [c for c in _charts(body) if c["metric"] == "T1"][0]["series"]
        assert any(a.get("uid", "").endswith(":404") for a in ser["attr"].values())
        assert all("pending" not in a for a in ser["attr"].values())

    def test_metric_meta_says_updating_while_pending(self, env, monkeypatch):
        s = env["snap"]
        s(2.0e-5)
        s(2.5e-5, "experiment", 401, "24_all_xy")
        # S10 C3: pending snapshot-writer checks -> preparing ledger, then the ready answer.
        from quam_state_manager.core import value_history, ramcache
        real = value_history.read
        def preparing(*a, **k):
            raise ramcache.Warming("fixture", "key", 0)
        monkeypatch.setattr(value_history, "read", preparing)
        d = env["client"].get("/topology/metric-meta").get_json()
        assert d["mode"] == "preparing" and d["updating"] is True and d["q"] == {}
        monkeypatch.setattr(value_history, "read", real)
        d = env["client"].get("/topology/metric-meta").get_json()
        assert d["mode"] == "ledger" and d["updating"] is False and d["q"]["T1"]["qA1"]
