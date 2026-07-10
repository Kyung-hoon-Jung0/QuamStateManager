"""Multi-folder Datasets: composite run identity (uid), cross-folder
aggregation, the global-latest new-run poll, and chip-identity gating for
Trends.

These exercise the feature that lets the Datasets table merge runs from every
registered data folder — where ``run_id`` (parsed from the run-folder name) is
unique only WITHIN a folder, so two folders can each hold a ``#250``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app


def _seed_run(root: Path, run_id: int, *, date="2026-05-28", hhmmss="010000",
              name="test_experiment", qubits=None, t1=8.0e-6,
              network=None, with_state=False) -> Path:
    """Create one run folder under ``root/<date>/#<run_id>_<name>_<hhmmss>``."""
    qubits = qubits or [f"q{run_id}"]
    date_dir = root / date
    date_dir.mkdir(parents=True, exist_ok=True)
    run = date_dir / f"#{run_id}_{name}_{hhmmss}"
    run.mkdir()
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": name, "status": "successful",
                     "run_start": f"{date}T01:00:00", "run_end": f"{date}T01:00:01"},
        "data": {"parameters": {"model": {"qubits": qubits}}, "outcomes": {}},
        "id": run_id, "parents": [], "created_at": f"{date}T01:00:00",
    }), encoding="utf-8")
    (run / "data.json").write_text(json.dumps({
        "fit_results": {qubits[0]: {"T1": t1}},
    }), encoding="utf-8")
    if with_state:
        qs = run / "quam_state"
        qs.mkdir()
        qs.joinpath("state.json").write_text(json.dumps({
            "qubits": {q: {} for q in qubits}, "qubit_pairs": {}}), encoding="utf-8")
        qs.joinpath("wiring.json").write_text(json.dumps({
            "network": network or {"host": "1.2.3.4", "cluster_name": "C"}}), encoding="utf-8")
    return run


def _app_with_folders(tmp_path: Path, folders: list[Path]):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    for f in folders:
        c.post("/workspace/add", data={"folder": str(f)})
    return app, c


def _script_json(body: str, el_id: str):
    m = re.search(rf'id="{el_id}"[^>]*>(.*?)</script>', body, re.S)
    assert m, f"missing <script id={el_id}>"
    return json.loads(m.group(1))


# --------------------------------------------------------------------------
# uid helpers (pure)
# --------------------------------------------------------------------------

class TestUid:
    def test_uid_round_trip(self):
        uid = routes._dataset_uid("a1b2c3d4", 250)
        assert uid == "a1b2c3d4:250"
        assert routes._split_dataset_uid(uid) == ("a1b2c3d4", 250)

    def test_split_rejects_malformed(self):
        assert routes._split_dataset_uid("250") is None       # bare int (old URL)
        assert routes._split_dataset_uid("") is None
        assert routes._split_dataset_uid("abc:notint") is None

    def test_folder_key_stable_and_distinct(self, tmp_path):
        a = tmp_path / "ExampleChip9Q"; a.mkdir()
        b = tmp_path / "HorizonQ"; b.mkdir()
        assert routes._folder_key(a) == routes._folder_key(a)   # stable
        assert routes._folder_key(a) != routes._folder_key(b)   # distinct paths


# --------------------------------------------------------------------------
# Cross-folder aggregation + collision-safe resolution
# --------------------------------------------------------------------------

class TestMultiFolderAggregation:
    def test_table_merges_folders_and_colliding_ids_resolve(self, tmp_path):
        fa = tmp_path / "chipA"
        fb = tmp_path / "chipB"
        _seed_run(fa, 250, qubits=["q0"], t1=5e-6)
        _seed_run(fb, 250, qubits=["q9"], t1=9e-6)   # SAME run_id, different folder
        app, c = _app_with_folders(tmp_path, [fa, fb])

        body = c.get("/datasets", headers={"HX-Request": "true"}).get_data(as_text=True)
        folders = _script_json(body, "ds-folders-data")
        assert {f["label"] for f in folders} == {"chipA", "chipB"}

        rows = _script_json(body, "ds-rows-data")
        assert sorted(r["id"] for r in rows) == [250, 250]    # both #250 present
        assert len({r["f"] for r in rows}) == 2               # distinct folder_keys

        with app.app_context():
            ka, kb = routes._folder_key(fa), routes._folder_key(fb)
        da = c.get(f"/dataset/{ka}:250", headers={"HX-Request": "true"}).get_data(as_text=True)
        db = c.get(f"/dataset/{kb}:250", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "q0" in da and "q9" not in da                  # uid resolved to chipA's run
        assert "q9" in db and "q0" not in db                  # …and chipB's

    def test_bad_uid_is_404(self, tmp_path):
        fa = tmp_path / "chipA"
        _seed_run(fa, 1)
        _app, c = _app_with_folders(tmp_path, [fa])
        assert c.get("/dataset/1", headers={"HX-Request": "true"}).status_code == 404  # bare int
        assert c.get("/dataset/deadbeef:999",
                     headers={"HX-Request": "true"}).status_code == 404  # unknown folder


# --------------------------------------------------------------------------
# New-run poll: globally-latest by timestamp, folder-aware uid
# --------------------------------------------------------------------------

class TestPollGlobalLatest:
    def test_poll_returns_global_latest_by_timestamp(self, tmp_path):
        fa = tmp_path / "chipA"
        fb = tmp_path / "chipB"
        _seed_run(fa, 100, date="2026-05-28", hhmmss="120000")   # high id, older
        _seed_run(fb, 5, date="2026-05-29", hhmmss="080000")     # low id, NEWER day
        app, c = _app_with_folders(tmp_path, [fa, fb])

        data = c.get("/datasets/poll").get_json()
        with app.app_context():
            kb = routes._folder_key(fb)
        # Latest by (date, time) — NOT max(run_id), which would wrongly pick #100.
        assert data["run_id"] == 5
        assert data["uid"] == f"{kb}:5"
        assert data["date"] == "2026-05-29"


# --------------------------------------------------------------------------
# Chip identity gating (Trends): same chip merges, different chips don't
# --------------------------------------------------------------------------

class TestChipIdentity:
    def test_same_chip_folders(self, tmp_path):
        a = tmp_path / "userA"
        b = tmp_path / "userB"
        net = {"host": "10.0.0.1", "cluster_name": "C1"}
        _seed_run(a, 1, qubits=["q1", "q2"], with_state=True, network=net)
        _seed_run(b, 2, qubits=["q1", "q2"], with_state=True, network=net)
        app, _c = _app_with_folders(tmp_path, [a, b])
        with app.test_request_context():
            active = routes._active_dataset_stores()
            assert len(active) == 2
            assert routes._folders_same_chip(active) == "same"

    def test_different_chip_folders(self, tmp_path):
        a = tmp_path / "chipA"
        b = tmp_path / "chipB"
        _seed_run(a, 1, qubits=["q1"], with_state=True,
                  network={"host": "1.1.1.1", "cluster_name": "A"})
        _seed_run(b, 1, qubits=["q9"], with_state=True,
                  network={"host": "2.2.2.2", "cluster_name": "B"})
        app, _c = _app_with_folders(tmp_path, [a, b])
        with app.test_request_context():
            active = routes._active_dataset_stores()
            assert routes._folders_same_chip(active) == "different"

    def test_same_chip_fallback_by_qubit_set_when_no_state(self, tmp_path):
        # No quam_state to fingerprint → fall back to the qubit-name set.
        a = tmp_path / "userA"
        b = tmp_path / "userB"
        _seed_run(a, 1, qubits=["q1", "q2"])
        _seed_run(b, 7, qubits=["q1", "q2"])
        app, _c = _app_with_folders(tmp_path, [a, b])
        with app.test_request_context():
            active = routes._active_dataset_stores()
            assert routes._folders_same_chip(active) == "same"


# --------------------------------------------------------------------------
# Datasets table click → left-sidebar highlight contract
#
# Clicking a run in the Datasets table highlights + reveals the matching entry
# in the left workspace tree. That JS sync relies on two server-rendered facts;
# lock both so a template change can't silently break the highlight:
#   1. the detail panel exposes the run's uid AND date (date drives the
#      cap-overflow "Show all" expansion);
#   2. the sidebar tree entry carries the SAME folder-aware uid the table uses.
# --------------------------------------------------------------------------

class TestSidebarHighlightContract:
    def test_detail_root_exposes_uid_and_date(self, tmp_path):
        fa = tmp_path / "chipA"
        _seed_run(fa, 250, qubits=["q0"], date="2026-05-28", with_state=True)
        app, c = _app_with_folders(tmp_path, [fa])
        with app.app_context():
            key = routes._folder_key(fa)
        html = c.get(f"/dataset/{key}:250",
                     headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'id="ds-detail-root"' in html
        assert f'data-uid="{key}:250"' in html
        assert 'data-date="2026-05-28"' in html       # cap-overflow retry key

    def test_sidebar_entry_uid_matches_table_uid(self, tmp_path):
        fa = tmp_path / "chipA"
        _seed_run(fa, 250, qubits=["q0"], date="2026-05-28", with_state=True)
        app, c = _app_with_folders(tmp_path, [fa])
        with app.app_context():
            key = routes._folder_key(fa)

        # The uid the table row click hands to /dataset/<uid>.
        body = c.get("/datasets", headers={"HX-Request": "true"}).get_data(as_text=True)
        rows = _script_json(body, "ds-rows-data")
        table_uids = {f'{r.get("f", "")}:{r["id"]}' for r in rows}
        assert f"{key}:250" in table_uids

        # The sidebar entry the JS matches that uid against (highlight target).
        tree = c.get("/workspace/tree").get_data(as_text=True)
        assert f'data-uid="{key}:250"' in tree
        assert "tree-entry-click" in tree
        # A RUN entry only VIEWS its dataset detail — it must NOT carry the
        # activating /workspace/select hx-post, which would flip the whole app
        # into the run's read-only archive. Users want the live chip they loaded
        # to stay the active editable context (the #1 complaint). Loading the
        # run's frozen state stays opt-in via the detail's "Load State" button.
        assert 'hx-post="/workspace/select"' not in tree


# --------------------------------------------------------------------------
# Candidate-folder memoization (finding B22)
#
# ``_dataset_candidate_folders`` did an O(runs) ``is_dir()`` stat storm and was
# called twice per ``_active_dataset_stores`` on every /datasets render and
# every ~60s changes-since poll. It must now memoize on a cheap workspace token
# so the stat-heavy rebuild runs only when the workspace layout actually
# changes — and a newly added data folder must still be discovered.
# --------------------------------------------------------------------------

class _WsRebuildSpy:
    """Wraps the real Workspace and counts ``all_entries`` accesses — the
    candidate rebuild always reads ``all_entries`` exactly once, so the count
    equals the number of stat-heavy rebuilds (token misses)."""

    def __init__(self, real):
        self._real = real
        self.rebuilds = 0

    @property
    def all_entries(self):
        self.rebuilds += 1
        return self._real.all_entries

    def __getattr__(self, name):
        return getattr(self._real, name)


class TestCandidateFolderMemoization:
    def _clear_cache(self):
        routes._dataset_candidates_cache.clear()

    def test_not_recomputed_when_token_unchanged(self, tmp_path, monkeypatch):
        self._clear_cache()
        fa = tmp_path / "chipA"
        _seed_run(fa, 1, qubits=["q0"])
        app, _c = _app_with_folders(tmp_path, [fa])

        # Pin the workspace token so it can't flip on its own (mtime jitter).
        monkeypatch.setattr(routes.HistoryManager, "_workspace_token",
                             staticmethod(lambda ws: "TOK"))

        with app.test_request_context():
            spy = _WsRebuildSpy(routes.current_app.config["workspace"])
            routes.current_app.config["workspace"] = spy
            first = routes._dataset_candidate_folders()
            assert spy.rebuilds == 1                  # cold call rebuilt once
            assert fa in first
            second = routes._dataset_candidate_folders()
            third = routes._dataset_candidate_folders()

        assert second == first and third == first     # same result
        assert spy.rebuilds == 1                       # NO further rebuild

    def test_recomputed_and_discovers_new_folder_when_token_changes(
            self, tmp_path, monkeypatch):
        self._clear_cache()
        fa = tmp_path / "chipA"
        fb = tmp_path / "chipB"
        _seed_run(fa, 1, qubits=["q0"])
        _seed_run(fb, 1, qubits=["q9"])
        app, c = _app_with_folders(tmp_path, [fa])

        token = {"v": "TOK-A"}
        monkeypatch.setattr(routes.HistoryManager, "_workspace_token",
                             staticmethod(lambda ws: token["v"]))

        with app.test_request_context():
            first = routes._dataset_candidate_folders()
            assert fb not in first                     # not registered yet

        # Register the new data folder AND flip the token (mirrors the shallow
        # stat token flipping when the workspace layout changes).
        c.post("/workspace/add", data={"folder": str(fb)})
        token["v"] = "TOK-B"

        with app.test_request_context():
            spy = _WsRebuildSpy(routes.current_app.config["workspace"])
            routes.current_app.config["workspace"] = spy
            second = routes._dataset_candidate_folders()
            assert spy.rebuilds == 1                   # token flipped → rebuilt
            assert fb in second                        # newly added folder found
            assert set(first).issubset(set(second))


# --------------------------------------------------------------------------
# /dataset/by-run/<run_id> — resolves a BARE run id (topology RB link) to its
# composite uid. Fixes the "Run 1139 not found" when the topology linked
# /dataset/<bare-id> (no folder context → uid parser rejected the missing ':').
# --------------------------------------------------------------------------

class TestDatasetByRun:
    def test_resolves_bare_run_id_to_composite_and_redirects(self, tmp_path):
        f = tmp_path / "data"
        _seed_run(f, 1139)
        app, c = _app_with_folders(tmp_path, [f])
        r = c.get("/dataset/by-run/1139")          # no follow
        assert r.status_code == 302
        key = routes._folder_key(f)
        assert r.headers["Location"].endswith(f"/dataset/{key}:1139")
        # and following it opens the run detail (200, not the 404 page)
        r2 = c.get("/dataset/by-run/1139", follow_redirects=True)
        assert r2.status_code == 200

    def test_unknown_run_id_is_clear_404(self, tmp_path):
        f = tmp_path / "data"
        _seed_run(f, 1139)
        app, c = _app_with_folders(tmp_path, [f])
        r = c.get("/dataset/by-run/999999")
        assert r.status_code == 404
        assert "in any loaded data folder" in r.get_data(as_text=True)

    def test_no_folders_loaded_is_clear_404(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        r = app.test_client().get("/dataset/by-run/1139")
        assert r.status_code == 404
        assert "loaded data folder" in r.get_data(as_text=True)


# --------------------------------------------------------------------------
# Datasets UX phase 0 (2026-07-02): the per-run click path must NOT pay the
# workspace-token stat-walk (~1.2s on 9p — the reported ~900ms run-click), and
# the run detail must carry the new navigation affordances.
# --------------------------------------------------------------------------

class TestRunClickFastPath:
    def test_store_for_folder_key_uses_cached_candidates(self, tmp_path, monkeypatch):
        """After the candidate cache is primed (any /datasets render), resolving
        a folder_key for a run click must not recompute the workspace token."""
        f = tmp_path / "data"
        _seed_run(f, 42)
        app, c = _app_with_folders(tmp_path, [f])
        c.get("/datasets", headers={"HX-Request": "true"})   # primes the cache
        from quam_state_manager.core.history import HistoryManager
        calls = {"n": 0}
        orig = HistoryManager._workspace_token
        def spy(ws):
            calls["n"] += 1
            return orig(ws)
        monkeypatch.setattr(HistoryManager, "_workspace_token", staticmethod(spy))
        with app.app_context():
            key = routes._folder_key(f)
        r = c.get(f"/dataset/{key}:42", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert calls["n"] == 0, "run click recomputed the workspace token (the 900ms bug)"

    def test_workspace_add_invalidates_candidates(self, tmp_path):
        """A folder added AFTER the cache primed must still resolve (fast path
        must not serve a stale list forever)."""
        f1 = tmp_path / "d1"; _seed_run(f1, 1)
        app, c = _app_with_folders(tmp_path, [f1])
        c.get("/datasets", headers={"HX-Request": "true"})
        f2 = tmp_path / "d2"; _seed_run(f2, 2)
        c.post("/workspace/add", data={"folder": str(f2)})
        with app.app_context():
            key2 = routes._folder_key(f2)
        assert c.get(f"/dataset/{key2}:2", headers={"HX-Request": "true"}).status_code == 200


class TestDetailNavAffordances:
    def test_detail_has_nav_and_fullpage_and_lazy_h5(self, tmp_path):
        f = tmp_path / "data"
        _seed_run(f, 7)
        app, c = _app_with_folders(tmp_path, [f])
        with app.app_context():
            key = routes._folder_key(f)
        body = c.get(f"/dataset/{key}:7", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "dsNavRun(-1)" in body and "dsNavRun(1)" in body   # prev/next run
        assert "dsOpenFullPage" in body                            # full-page expand
        assert 'hx-trigger="load"' not in body, \
            "hidden-tab eager load re-introduced (H5 opened on every click)"


class TestPhase2Compare:
    """Datasets phase 2: 'vs prev same-experiment' one-click + the 8-run cap."""

    def _seed_series(self, tmp_path):
        f = tmp_path / "data"
        _seed_run(f, 10, name="rabi", hhmmss="010000")
        _seed_run(f, 11, name="t1", hhmmss="020000")
        _seed_run(f, 12, name="rabi", hhmmss="030000")
        app, c = _app_with_folders(tmp_path, [f])
        with app.app_context():
            key = routes._folder_key(f)
        return app, c, key

    def test_compare_prev_resolves_same_experiment(self, tmp_path):
        app, c, key = self._seed_series(tmp_path)
        r = c.get(f"/dataset/{key}:12/compare-prev")
        assert r.status_code == 302
        # prev SAME-EXPERIMENT run is 10 (rabi), NOT 11 (t1, the id-order prev)
        assert f"ids={key}%3A10%2C{key}%3A12" in r.headers["Location"] \
            or f"ids={key}:10,{key}:12" in r.headers["Location"]

    def test_compare_prev_first_run_is_friendly(self, tmp_path):
        app, c, key = self._seed_series(tmp_path)
        r = c.get(f"/dataset/{key}:10/compare-prev")
        assert r.status_code == 200
        assert "No earlier run" in r.get_data(as_text=True)

    def test_compare_cap_is_8(self, tmp_path):
        f = tmp_path / "data"
        for i in range(1, 10):
            _seed_run(f, i, hhmmss=f"0{i}0000")
        app, c = _app_with_folders(tmp_path, [f])
        with app.app_context():
            key = routes._folder_key(f)
        ids8 = ",".join(f"{key}:{i}" for i in range(1, 9))
        assert c.get(f"/datasets/compare?ids={ids8}",
                     headers={"HX-Request": "true"}).status_code == 200
        ids9 = ids8 + f",{key}:9"
        assert "2-8" in c.get(f"/datasets/compare?ids={ids9}",
                              headers={"HX-Request": "true"}).get_data(as_text=True)
