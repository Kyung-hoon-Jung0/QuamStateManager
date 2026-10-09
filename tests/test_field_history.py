"""Ledger value-history popover: Use, current values, run links and isolation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app
from tests.ledger_fixture import declare_root

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"},
           "ports": {"mw_outputs": {"con1": {"1": {"2": {"band": 1}}}}}}


def _state(f01=5.0e9, anh=200e6, amp=0.1, extra=None):
    q = {"id": "qA1", "f_01": f01, "anharmonicity": anh,
         "xy": {"operations": {"x180_DragCosine": {"amplitude": amp}}}}
    if extra:
        q.update(extra)
    return {"qubits": {"qA1": q}, "qubit_pairs": {},
            "active_qubit_names": ["qA1"]}


def _write_chip(folder: Path, state: dict, wiring: dict | None = None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring or _WIRING),
                                        encoding="utf-8")


def _seed_run(root: Path, run_id: int, name="08_qubit_spectroscopy",
              date="2026-07-29", hhmmss="010000",
              quam_state: dict | None = None,
              quam_wiring: dict | None = None, patches=None) -> Path:
    """*patches*: the node's own record of what it wrote -- a Data button is
    offered only for a run shown to have WRITTEN the value (2026-09-29)."""
    run = root / date / f"#{run_id}_{name}_{hhmmss}"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": name, "status": "successful",
                     "run_start": f"{date}T01:00:00",
                     "run_end": f"{date}T01:00:01"},
        "data": {"parameters": {"model": {"qubits": ["qA1"]}}, "outcomes": {}},
        "id": run_id, "parents": [], "created_at": f"{date}T01:00:00",
        **({"patches": patches} if patches is not None else {}),
    }), encoding="utf-8")
    (run / "data.json").write_text("{}", encoding="utf-8")
    if quam_state is not None:
        _write_chip(run / "quam_state", quam_state, quam_wiring)
    return run


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state())
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    r = c.post("/load", data={"folder": str(live)})
    assert r.status_code in (200, 302)
    return {"app": app, "client": c, "live": live,
            "hm": app.config["history_manager"], "tmp": tmp_path}


def _mutate_and_snap(env, state, trigger="manual", **kw):
    _write_chip(env["live"], state)
    meta = env["hm"].check_and_snapshot(str(env["live"]), trigger,
                                        force=True, **kw)
    assert meta is not None
    return meta


# S10 C4: old -> new, retire tests whose whole subject was the removed snapshot tiers.


class TestFieldHistoryRoute:
    def test_panel_renders_values_use_and_current(self, env):
        c = env["client"]
        _mutate_and_snap(env, _state(f01=5.0e9))   # == loaded store value
        # S10 C3: fabricated run attribution -> observed capture, retaining Use/current precision.
        _mutate_and_snap(env, _state(f01=5.1e9))
        r = c.get("/field/history?path=qubits.qA1.f_01")
        assert r.status_code == 200
        html = r.data.decode()
        assert 'data-value="5100000000.0"' in html      # Use fills full precision
        assert "writer unknown" in html                 # provenance shown honestly
        assert 'id="fh-chart-data"' in html             # mini trend payload
        assert "not proven" in html or "writer unknown" in html
        # the store still holds the ORIGINAL 5.0e9 → that row is "current":
        # no Use button for it, badge present
        # S10 C3: any matching historical row -> newest matching ledger point; now stays a separate fact.
        assert html.count("fh-use") == 2
        assert "fh-currentline" in html and "fh-now" not in html

    def test_data_button_only_for_registered_run(self, env):
        c = env["client"]
        data_root = env["tmp"] / "data"
        # S10 C3: snapshot run hints -> declared ledger runs with a saved pair and patch proof.
        _seed_run(data_root, 30, hhmmss="000000", quam_state=_state())
        run = _seed_run(data_root, 31, quam_state=_state(f01=5.1e9), patches=[{"op": "replace", "path": "/quam/qubits/qA1/f_01", "value": 5.1e9}])
        c.post("/workspace/add", data={"folder": str(data_root)})
        declare_root(c, data_root)
        _mutate_and_snap(env, _state(f01=5.0e9))
        _mutate_and_snap(env, _state(f01=5.1e9), trigger="experiment",
                         experiment_name="08_qubit_spectroscopy", run_id=31,
                         experiment_folder_path=str(run))
        # a second change whose run folder is NOT under any registered root
        _mutate_and_snap(env, _state(f01=5.2e9), trigger="experiment",
                         experiment_name="somewhere_else", run_id=99,
                         experiment_folder_path=str(env["tmp"] / "elsewhere" / "#99_x_0"))
        r = c.get("/field/history?path=qubits.qA1.f_01")
        html = r.data.decode()
        key = routes_mod._folder_key(data_root)
        assert f'hx-get="/dataset/{key}:31"' in html
        assert 'hx-target="#inspector-pane"' in html
        assert ":99" not in html, "unregistered run folder must not get a Data link"

    def test_no_chip_400(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst2"))
        r = app.test_client().get("/field/history?path=qubits.qA1.f_01")
        assert r.status_code == 400

    def test_missing_path_400(self, env):
        assert env["client"].get("/field/history").status_code == 400

    def test_inspector_and_bulk_carry_the_button(self, env):
        c = env["client"]
        html = c.get("/qubit/qA1").data.decode()
        assert "FieldHistory.openInspector" in html
        assert "field-hist-btn" in html


class TestRunsTier:
    """docs/20 v2 Step 6: the popover reads the workspace runs' own
    quam_state copies directly — today's values appear with guaranteed Data
    links even when Param History ingestion never ran (the deviceC report:
    33 un-ingested runs). Foreign chips are fingerprint-gated out; a shared
    extras chip name is definitive even across a host move."""

    def _run_state(self, f01):
        return _state(f01=f01)

    def test_run_values_appear_without_ingestion(self, env):
        c = env["client"]
        data_root = env["tmp"] / "data"
        _seed_run(data_root, 31, hhmmss="010000",
                  quam_state=self._run_state(5.31e9), quam_wiring=_WIRING)
        _seed_run(data_root, 32, name="06_ramsey", hhmmss="020000",
                  quam_state=self._run_state(5.32e9), quam_wiring=_WIRING)
        # a FOREIGN chip's run in the same root — must never appear
        _seed_run(data_root, 99, name="foreign", hhmmss="030000",
                  quam_state={"qubits": {"qZ9": {"id": "qZ9", "f_01": 9.9e9}},
                              "qubit_pairs": {}, "active_qubit_names": ["qZ9"]},
                  quam_wiring={"network": {"host": "9.9.9.9",
                                           "cluster_name": "X"}})
        c.post("/workspace/add", data={"folder": str(data_root)})
        # S10 C3: request-time run scan -> declared ledger, no Param History ingestion needed.
        declare_root(c, data_root)
        r = c.get("/field/history?path=qubits.qA1.f_01")
        assert r.status_code == 200
        html = r.data.decode()
        assert 'data-value="5320000000.0"' in html
        assert "06_ramsey" in html and "#32" in html
        key = routes_mod._folder_key(data_root)
        assert f'hx-get="/dataset/{key}:32?via=saved"' in html, \
            "a saved-run link carries its unproven context"
        assert "9900000000" not in html, "foreign chip's runs are gated out"
        assert "saved" in html and "not proven" in html

    def test_name_match_beats_network_move(self, env, tmp_path):
        """Both sides declare the same extras chip name but hosts differ (a
        re-hosted setup): the name is definitive — the run still counts."""
        c = env["client"]
        # name the LOADED chip (staged into the working copy is enough — the
        # runs tier reads the store dicts)
        c.post("/chip-name/set", data={"name": "deviceC"})
        data_root = env["tmp"] / "data2"
        named = self._run_state(5.55e9)
        named["extras"] = {"chip_name": "deviceC"}
        _seed_run(data_root, 41, hhmmss="040000", quam_state=named,
                  quam_wiring={"network": {"host": "10.9.9.9",
                                           "cluster_name": "MOVED"}})
        c.post("/workspace/add", data={"folder": str(data_root)})
        # S10 C3: implicit run scan -> explicit ledger root, the declared identity still gates runs.
        declare_root(c, data_root)
        html = c.get("/field/history?path=qubits.qA1.f_01").data.decode()
        assert 'data-value="5550000000.0"' in html

    def test_snapshot_and_run_series_merge_in_time_order(self, env):
        c = env["client"]
        _mutate_and_snap(env, _state(f01=5.0e9))     # snapshot tier (older)
        data_root = env["tmp"] / "data3"
        # far-future run date so it is strictly newer than the just-taken
        # snapshot regardless of the wall clock
        _seed_run(data_root, 51, date="2026-12-31", hhmmss="120000",
                  quam_state=self._run_state(5.77e9), quam_wiring=_WIRING)
        c.post("/workspace/add", data={"folder": str(data_root)})
        # S10 C3: merged snapshot/run scan -> observed and run events in one ledger timeline.
        declare_root(c, data_root)
        r = c.get("/field/history?path=qubits.qA1.f_01")
        html = r.data.decode()
        i_run = html.find('data-value="5770000000.0"')
        i_snap = html.find('title="5000000000.0"')
        assert i_run != -1 and i_snap != -1
        assert i_run < i_snap, "newest (run) row renders first"


class TestRunCacheChipIndependence:
    def test_switching_chips_regates_run_verdicts(self, tmp_path):
        """audit-r10 F-A (repro-confirmed): the run caches hold only
        chip-independent facts — the include verdict is re-derived per call,
        so chip A's runs never leak into chip B's popover after a chip
        switch, and B's own runs are never suppressed by A's warm cache."""
        from quam_state_manager.web.app import create_app
        root = tmp_path / "data"
        st_a = _state(f01=7.1e9); st_a["extras"] = {"chip_name": "alpha"}
        st_b = _state(f01=7.2e9); st_b["extras"] = {"chip_name": "beta"}
        _seed_run(root, 31, quam_state=st_a, hhmmss="010000")
        _seed_run(root, 32, quam_state=st_b, hhmmss="020000")
        live_a = tmp_path / "chips" / "a"
        live_b = tmp_path / "chips" / "b"
        sa = _state(); sa["extras"] = {"chip_name": "alpha"}
        sb = _state(); sb["extras"] = {"chip_name": "beta"}
        _write_chip(live_a, sa)
        _write_chip(live_b, sb)
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        assert c.post("/load", data={"folder": str(live_a)}).status_code in (200, 302)
        c.post("/workspace/add", data={"folder": str(root)})
        # S10 C3: cached run-scan verdicts -> per-chip ledger views, preserving cross-chip isolation.
        declare_root(c, root)
        h1 = c.get("/field/history?path=qubits.qA1.f_01").data.decode()
        assert 'data-value="7100000000.0"' in h1
        # S10 C3: foreign rows discarded by a scan -> flagged ledger evidence, never a proven writer.
        points_a = c.get("/api/agent/field-history?path=qubits.qA1.f_01").get_json()["history"]["points"]
        foreign_a = [p for p in points_a if p["value"] == 7.2e9]
        assert foreign_a and all(p["provenance"] == "run_uncertain_chip" and not p.get("uid") for p in foreign_a)
        assert "chip uncertain" in h1
        assert c.post("/load", data={"folder": str(live_b)}).status_code in (200, 302)
        declare_root(c, root)
        h2 = c.get("/field/history?path=qubits.qA1.f_01").data.decode()
        assert 'data-value="7200000000.0"' in h2, "B's own run suppressed by A's cache"
        points_b = c.get("/api/agent/field-history?path=qubits.qA1.f_01").get_json()["history"]["points"]
        foreign_b = [p for p in points_b if p["value"] == 7.1e9]
        assert foreign_b and all(p["provenance"] == "run_uncertain_chip" and not p.get("uid") for p in foreign_b)
        assert "chip uncertain" in h2


class TestUidDeepestRoot:
    def test_uid_prefers_deepest_containing_root(self, tmp_path):
        """audit-r10 F-G: with nested registered roots the run's Data uid
        must key the DEEPEST one — the shallow root's DatasetStore holds no
        runs at depth 3, so its uid would 404."""
        from quam_state_manager.web.app import create_app
        from quam_state_manager.web import routes as routes_mod
        outer = tmp_path / "ws"
        chip_root = outer / "chipX"
        _seed_run(chip_root, 61, quam_state=_state(f01=7.3e9), hhmmss="030000",
                  patches=[{"op": "replace", "path": "/quam/qubits/qA1/f_01", "value": 7.3e9}])
        live = tmp_path / "chips" / "live"
        _write_chip(live, _state())
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
        c.post("/workspace/add", data={"folder": str(outer)})
        c.post("/workspace/add", data={"folder": str(chip_root)})
        # S10 C3: run-scan fixture -> declared ledger root, retaining deepest-root UID resolution.
        declare_root(c, chip_root)
        html = c.get("/field/history?path=qubits.qA1.f_01").data.decode()
        deep = routes_mod._folder_key(chip_root)
        shallow = routes_mod._folder_key(outer)
        assert f"/dataset/{deep}:61" in html
        assert f"/dataset/{shallow}:61" not in html
