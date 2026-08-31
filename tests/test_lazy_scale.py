"""docs/142 — RAM + lazy loading at 5,000-run scale.

Pins the four mechanisms of the scale round, each measured on a customer-shaped
5,000-run archive before shipping (numbers in docs/142):

* listing-first scan: ``add_root(defer_parse=True)`` publishes name-derived
  stubs immediately and hydrates node.json on a daemon thread
  (cold first paint 10.7 s -> ~1.7 s);
* the persistent listing cache: a second session opens the same root fully
  parsed without a walk, then a background verify catches disk drift;
* /param-history renders from SQLite alone (T1/T2 default), the O(N)
  alignment scan living in its own lazy fragment (16.9 s -> 0.16 s);
* extract_property_history(compress="changes"): an unchanged series is
  first+last, a change keeps both step edges (Chip Status Trends stops
  plotting one marker per run for values that never moved).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from quam_state_manager.core import scanner
from quam_state_manager.core.scanner import Workspace


def _mk_run(root: Path, date: str, rid: int, name: str = "04_power_rabi",
            status: str = "finished", qubit: str = "q1") -> Path:
    run = root / date / f"#{rid}_{name}_1200{rid % 60:02d}"
    qs = run / "quam_state"
    qs.mkdir(parents=True)
    (qs / "state.json").write_text(json.dumps({"qubits": {qubit: {"f_01": 1}}}),
                                   encoding="utf-8")
    (qs / "wiring.json").write_text("{}", encoding="utf-8")
    (run / "node.json").write_text(json.dumps({
        "id": rid, "created_at": f"{date}T12:00:{rid % 60:02d}+00:00",
        "metadata": {"name": name, "status": status},
        "data": {"parameters": {"model": {"qubits": [qubit]}}},
    }), encoding="utf-8")
    return qs


def _wait_hydrated(ws: Workspace, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while ws.hydrating_roots():
        assert time.time() < deadline, "hydration never finished"
        time.sleep(0.02)


class TestListingFirst:
    def test_defer_returns_name_derived_stubs_then_hydrates(self, tmp_path):
        for rid in (1, 2, 3):
            _mk_run(tmp_path, "2026-03-01", rid)
        ws = Workspace()
        entries = ws.add_root(tmp_path, defer_parse=True)
        assert len(entries) == 3
        # the stub knows everything the folder NAME carries...
        stub = min(entries, key=lambda e: e.run_id)
        assert (stub.run_id, stub.experiment_name, stub.date_str) == (
            1, "04_power_rabi", "2026-03-01")
        assert stub.timestamp.startswith("2026-03-01T12:00:")
        # ...and is honest about what it does not know yet
        assert stub.needs_parse and stub.status == "" and stub.qubits == []
        assert ws.hydrating_roots() == {str(tmp_path.resolve())}

        _wait_hydrated(ws)
        hydrated = ws.get_flat_list()
        assert [e.needs_parse for e in hydrated] == [False] * 3
        assert {e.status for e in hydrated} == {"finished"}
        assert {tuple(e.qubits) for e in hydrated} == {("q1",)}

    def test_hydration_bumps_version_for_the_sidebar_poll(self, tmp_path):
        _mk_run(tmp_path, "2026-03-01", 1)
        ws = Workspace()
        ws.add_root(tmp_path, defer_parse=True)
        v_at_publish = ws.version
        _wait_hydrated(ws)
        assert ws.version > v_at_publish

    def test_defer_on_a_standalone_chip_folder_stays_synchronous(self, tmp_path):
        qs = tmp_path / "quam_state"
        qs.mkdir()
        (qs / "state.json").write_text("{}", encoding="utf-8")
        (qs / "wiring.json").write_text("{}", encoding="utf-8")
        ws = Workspace()
        entries = ws.add_root(qs, defer_parse=True)
        assert len(entries) == 1 and not entries[0].needs_parse
        assert not ws.hydrating_roots()

    def test_sync_add_root_is_byte_identical_to_hydrated_defer(self, tmp_path):
        for rid in (1, 2):
            _mk_run(tmp_path, "2026-03-02", rid, status="error" if rid == 2 else "finished")
        ws_sync, ws_defer = Workspace(), Workspace()
        ws_sync.add_root(tmp_path)
        ws_defer.add_root(tmp_path, defer_parse=True)
        _wait_hydrated(ws_defer)
        key = lambda e: (e.run_id, e.experiment_name, e.timestamp, e.status,
                         tuple(e.qubits), e.date_str)
        assert sorted(map(key, ws_sync.get_flat_list())) == \
               sorted(map(key, ws_defer.get_flat_list()))


class TestListingCache:
    def test_second_session_opens_parsed_from_cache(self, tmp_path):
        root = tmp_path / "data"
        _mk_run(root, "2026-03-01", 7)
        cache = tmp_path / "cache"
        ws1 = Workspace(); ws1.cache_dir = cache
        ws1.add_root(root, defer_parse=True)
        _wait_hydrated(ws1)
        deadline = time.time() + 10
        while not list(cache.glob("ws_*.json")):
            assert time.time() < deadline, "listing cache never written"
            time.sleep(0.05)

        ws2 = Workspace(); ws2.cache_dir = cache
        entries = ws2.add_root(root, defer_parse=True)
        # cache-served: already parsed, no hydration pass at all
        assert [e.needs_parse for e in entries] == [False]
        assert entries[0].status == "finished" and entries[0].qubits == ["q1"]
        assert not ws2.hydrating_roots()

    def test_background_verify_catches_disk_drift(self, tmp_path):
        root = tmp_path / "data"
        _mk_run(root, "2026-03-01", 7)
        cache = tmp_path / "cache"
        ws1 = Workspace(); ws1.cache_dir = cache
        ws1.add_root(root, defer_parse=True)
        _wait_hydrated(ws1)
        deadline = time.time() + 10
        while not list(cache.glob("ws_*.json")):
            assert time.time() < deadline
            time.sleep(0.05)
        _mk_run(root, "2026-03-02", 8)      # lands AFTER the cached session

        ws2 = Workspace(); ws2.cache_dir = cache
        ws2.add_root(root, defer_parse=True)
        deadline = time.time() + 20
        while len(ws2.get_flat_list()) != 2:
            assert time.time() < deadline, (
                "cached-root verify never picked up the new run")
            time.sleep(0.05)
        assert {e.run_id for e in ws2.get_flat_list()} == {7, 8}

    def test_no_cache_dir_means_no_cache_files(self, tmp_path):
        root = tmp_path / "data"
        _mk_run(root, "2026-03-01", 7)
        ws = Workspace()          # cache_dir stays None (every test's default)
        ws.add_root(root, defer_parse=True)
        _wait_hydrated(ws)
        assert not list(tmp_path.rglob("ws_*.json"))

    def test_corrupt_cache_reads_as_a_miss(self, tmp_path):
        root = tmp_path / "data"
        _mk_run(root, "2026-03-01", 7)
        cache = tmp_path / "cache"
        ws1 = Workspace(); ws1.cache_dir = cache
        p = ws1._cache_path(root.resolve())
        p.parent.mkdir(parents=True)
        p.write_text("{ not json", encoding="utf-8")
        entries = ws1.add_root(root, defer_parse=True)
        assert len(entries) == 1          # fell back to the walk
        _wait_hydrated(ws1)


class TestCompressChanges:
    """extract_property_history(compress='changes') — docs/142 D."""

    def _hm(self, tmp_path, values):
        # rows straight into the index, the same fixture style as
        # test_history.py::test_no_downsample_returns_everything
        import sqlite3
        from quam_state_manager.core.history import (
            HistoryManager, _ensure_param_history_schema)
        hm = HistoryManager(tmp_path / "instance")
        qs = tmp_path / "quam_state"
        qs.mkdir()
        (qs / "state.json").write_text("{}", encoding="utf-8")
        (qs / "wiring.json").write_text("{}", encoding="utf-8")
        hm._history_dir(qs).mkdir(parents=True, exist_ok=True)
        idx = hm._index_path(qs)
        _ensure_param_history_schema(idx)
        conn = sqlite3.connect(str(idx), isolation_level=None)
        try:
            conn.executemany(
                "INSERT OR REPLACE INTO param_history "
                "(timestamp, qubit, property, value, raw_pointer, trigger, run_id, experiment) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(f"20260301_1200{i:02d}_000", "q1", "T1", v, None, "save", i, "t")
                 for i, v in enumerate(values)])
        finally:
            conn.close()
        return hm, qs

    def test_unchanged_series_is_exactly_first_and_last(self, tmp_path):
        hm, qs = self._hm(tmp_path, [5.0] * 6)
        rows = hm.extract_property_history(qs, ["T1"], compress="changes")
        (bucket,) = [r for r in rows if r["qubit"] == "q1"]
        assert [p["value"] for p in bucket["values"]] == [5.0, 5.0]
        assert bucket["values"][0]["timestamp"] == "20260301_120000_000"
        assert bucket["values"][-1]["timestamp"] == "20260301_120005_000"

    def test_a_change_keeps_both_step_edges(self, tmp_path):
        # flat(3) -> step -> flat(3): the kept points must draw flat-then-step,
        # not a slope that never happened
        hm, qs = self._hm(tmp_path, [1.0, 1.0, 1.0, 2.0, 2.0, 2.0])
        rows = hm.extract_property_history(qs, ["T1"], compress="changes")
        (bucket,) = [r for r in rows if r["qubit"] == "q1"]
        assert [p["value"] for p in bucket["values"]] == [1.0, 1.0, 2.0, 2.0]
        kept_ts = [p["timestamp"] for p in bucket["values"]]
        assert kept_ts == ["20260301_120000_000", "20260301_120002_000",
                           "20260301_120003_000", "20260301_120005_000"]

    def test_default_stays_uncompressed(self, tmp_path):
        hm, qs = self._hm(tmp_path, [5.0] * 6)
        rows = hm.extract_property_history(qs, ["T1"])
        (bucket,) = [r for r in rows if r["qubit"] == "q1"]
        assert len(bucket["values"]) == 6

    def test_nan_stretch_collapses(self, tmp_path):
        hm, qs = self._hm(tmp_path, [float("nan")] * 5)
        rows = hm.extract_property_history(qs, ["T1"], compress="changes")
        (bucket,) = [r for r in rows if r["qubit"] == "q1"]
        assert len(bucket["values"]) == 2   # NaN != NaN must not explode this


class TestParamHistoryDeferredAlignment:
    """/param-history renders from SQLite alone; the alignment scan lives in
    the lazy fragment (docs/142 C)."""

    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        from quam_state_manager.web.app import create_app
        monkeypatch.setenv("SM_DISABLE_ENV_WARMUP", "1")
        qs = _mk_run(tmp_path / "data", "2026-03-01", 1)
        app = create_app()
        app.config["TESTING"] = True
        c = app.test_client()
        r = c.post("/load", data={"folder": str(qs)},
                   headers={"Origin": "http://localhost"})
        assert r.status_code in (200, 302)
        return c

    def test_get_never_runs_the_alignment_scan(self, client, monkeypatch):
        from quam_state_manager.core.history import HistoryManager
        def boom(*a, **k):
            raise AssertionError("scan_workspace_alignment ran on the GET")
        monkeypatch.setattr(HistoryManager, "scan_workspace_alignment", boom)
        r = client.get("/param-history")
        assert r.status_code == 200

    def test_page_carries_the_lazy_fragment_slot(self, client):
        html = client.get("/param-history").data.decode("utf-8")
        assert 'id="ph-alignment-slot"' in html
        assert 'hx-get="/param-history/alignment' in html

    def test_fragment_returns_counts_and_rearm_script(self, client):
        html = client.get("/param-history/alignment").data.decode("utf-8")
        assert "data-importable-count=" in html
        assert "paramHistoryMaybeAutoBackfill" in html

    def test_default_props_are_t1_t2_only(self, client):
        from quam_state_manager.core.history import DEFAULT_VISIBLE_PROPERTIES
        assert DEFAULT_VISIBLE_PROPERTIES == ("T1", "T2ramsey", "T2echo")
        html = client.get("/param-history").data.decode("utf-8")
        # the grid renders only the visible three; the picker still offers all
        assert 'data-prop="f_01"' not in html or "f_01" in html  # picker text
        for p in DEFAULT_VISIBLE_PROPERTIES:
            assert p in html


class TestLazySidebarGroups:
    """docs/142 B4 — collapsed date groups ship empty and fetch on open."""

    @pytest.fixture()
    def client(self, tmp_path, monkeypatch):
        from quam_state_manager.web.app import create_app
        from quam_state_manager.web import routes as routes_mod
        monkeypatch.setenv("SM_DISABLE_ENV_WARMUP", "1")
        # docs/148b: small trees render eagerly; force the lazy path for the
        # fixture's 4-run tree so these pins keep testing it
        monkeypatch.setattr(routes_mod, "_LAZY_GROUP_MIN_ENTRIES", 0)
        self.root = tmp_path / "data"
        self.qs = _mk_run(self.root, "2026-03-01", 1)
        for rid in range(2, 5):
            _mk_run(self.root, "2026-03-02", rid)
        app = create_app()
        app.config["TESTING"] = True
        c = app.test_client()
        r = c.post("/load", data={"folder": str(self.qs)},
                   headers={"Origin": "http://localhost"})
        assert r.status_code in (200, 302)
        ws = app.config["workspace"]
        deadline = time.time() + 15
        while ws.hydrating_roots():
            assert time.time() < deadline
            time.sleep(0.02)
        return c

    def test_inactive_group_is_lazy_and_active_group_is_eager(self, client):
        html = client.get("/workspace/tree").data.decode("utf-8")
        assert 'data-lazy-group="1"' in html          # 2026-03-02 (inactive)
        # the ACTIVE run's group must carry its rows at paint
        # (.tree-branch-active needs the entry present)
        assert html.count("tree-entry-click") >= 1
        assert 'data-tpath="2026-03-01"' in html
        lazy_zone = html.split('data-tpath="2026-03-02"')[1]
        assert "tree-lazy-hint" in lazy_zone.split("</details>")[0]

    def test_group_fetch_returns_capped_rows(self, client):
        r = client.get("/workspace/tree/group", query_string={
            "capped": "1", "root": str(self.root.resolve()),
            "tpath": "2026-03-02"})
        html = r.data.decode("utf-8")
        assert html.count("tree-entry-click") == 3
        assert "tree-show-more-btn" not in html       # under the 50 cap

    def test_filtered_tree_renders_matches_eagerly(self, client):
        html = client.get("/workspace/tree?name=rabi").data.decode("utf-8")
        assert 'data-lazy-group="1"' not in html
        assert html.count("tree-entry-click") == 4

    def test_small_trees_render_eagerly(self, client, monkeypatch):
        """docs/148b: at or below the floor the whole tree ships eagerly --
        lazy groups exist for the 5,000-run archives, not 5-run chips."""
        from quam_state_manager.web import routes as routes_mod
        monkeypatch.setattr(routes_mod, "_LAZY_GROUP_MIN_ENTRIES", 200)
        html = client.get("/workspace/tree").data.decode("utf-8")
        assert 'data-lazy-group="1"' not in html
        assert html.count("tree-entry-click") == 4
