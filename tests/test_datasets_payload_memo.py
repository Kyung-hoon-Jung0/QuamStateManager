"""RAM P7 -- the Datasets page payload served from RAM (routes._DATASETS_PAYLOAD).

The rows JSON (3.5 MB at 4,121 runs) and every aggregate beside it are a
function of the active stores alone; they are memoized on each store's
identity + both generations + the view + the date tab, validated on read.
Pinned: a randomized event sequence where every served payload equals a cold
recompute, a sweep over every token component (drop one, a sequence goes
red), and the route serving the memo (no recompute on a repeat request).
"""
from __future__ import annotations

import json
import os
import random
import shutil
from pathlib import Path

import pytest

from quam_state_manager.core.dataset import DatasetStore
from quam_state_manager.web import routes as R
from quam_state_manager.web.app import create_app

from tests.test_trend_index import A, B, _run


@pytest.fixture(autouse=True)
def _fresh():
    R._DATASETS_PAYLOAD.clear()
    yield
    R._DATASETS_PAYLOAD.clear()


def _bump_dir(d: Path) -> None:
    st = d.stat()
    os.utime(d, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))


def _active(stores: list[tuple[str, DatasetStore]]) -> list[dict]:
    return [{"key": k, "path": str(s.folder_path), "label": Path(s.folder_path).name,
             "store": s} for k, s in stores]


def _served(active, coll, date) -> dict:
    return json.loads(json.dumps(R._datasets_payload(active, coll, date), default=list))


def _cold(active, coll, date) -> dict:
    return json.loads(json.dumps(R._datasets_payload_compute(active, coll, date), default=list))


def _all_views_match(active, dates) -> list:
    bad = []
    for coll in (False, True):
        for d in (None, *dates):
            if _served(active, coll, d) != _cold(active, coll, d):
                bad.append((coll, d))
    return bad


class TestRandomSequence:
    @pytest.mark.parametrize("seed", [11, 12, 13])
    def test_every_served_payload_equals_a_cold_recompute(self, tmp_path, seed):
        rng = random.Random(seed)
        roots = [tmp_path / "d1", tmp_path / "d2"]
        rid = 0
        for r in roots:
            for _ in range(5):
                rid += 1
                _run(r, rid, A if rid % 2 else B, date="2026-09-01")
        stores = [(f"k{i}", DatasetStore(r)) for i, r in enumerate(roots)]
        dates = ["2026-09-01", "2026-09-02"]
        active = _active(stores)
        views = ["2026-09-02"]           # + None: 4 slots, inside the memo's 6
        assert _all_views_match(active, views) == []
        hits0 = R._DATASETS_PAYLOAD.hits
        for step in range(60):
            ev = rng.choice(["run", "run", "tag", "untag", "note", "bookmark",
                             "delete", "status", "drop_folder", "add_folder", "noop"])
            i = rng.randrange(len(stores))
            key, store = stores[i]
            runs = sorted(store.runs) if store.runs else []
            if ev == "run":
                rid += 1
                d = rng.choice(dates)
                _run(Path(store.folder_path), rid, rng.choice([A, B]), date=d)
                _bump_dir(Path(store.folder_path) / d)
                store.rescan_if_stale()
            elif ev == "tag" and runs:
                store.add_tag(rng.choice(runs), rng.choice(["good", "bad", "favorite"]))
            elif ev == "untag" and runs:
                r = rng.choice(runs)
                for t in list(store.runs[r].tags):
                    store.remove_tag(r, t)
            elif ev == "note" and runs:
                store.set_note(rng.choice(runs), f"n{step}")
            elif ev == "bookmark" and runs:
                store.toggle_bookmark(rng.choice(runs))
            elif ev == "delete" and runs:
                r = rng.choice(runs)
                shutil.rmtree(store.runs[r].folder_path)
                _bump_dir(Path(store.folder_path) / store.runs[r].date)
                store.rescan_if_stale()
            elif ev == "status" and runs:
                r = rng.choice(runs)
                p = Path(store.runs[r].folder_path) / "node.json"
                d = json.loads(p.read_text(encoding="utf-8"))
                d["metadata"]["status"] = rng.choice(["failed", "successful", "error"])
                p.write_text(json.dumps(d), encoding="utf-8")
                _bump_dir(p.parent)
                _bump_dir(p.parent.parent)
                store.rescan_if_stale()
            elif ev == "drop_folder" and len(stores) > 1:
                stores = stores[:1]
            elif ev == "add_folder" and len(stores) == 1:
                stores = stores + [("k1", DatasetStore(roots[1]))]
            active = _active(stores)
            bad = _all_views_match(active, views)
            assert bad == [], f"step {step} ({ev}): {bad}"
        # not vacuous: the memo really served most of those answers
        assert R._DATASETS_PAYLOAD.hits - hits0 > 60


class TestTokenSweep:
    """Each token component, alone: an event that moves only it must change
    the served payload."""

    def _setup(self, tmp_path):
        root = tmp_path / "d"
        for rid in range(1, 4):
            _run(root, rid, A, date="2026-09-01")
        s = DatasetStore(root)
        return root, s, _active([("k", s)])

    def test_a_new_run_is_served(self, tmp_path):          # generation
        root, s, active = self._setup(tmp_path)
        before = _served(active, False, None)
        m0 = s.meta_generation
        _run(root, 9, A, date="2026-09-01")
        _bump_dir(root / "2026-09-01")
        s.rescan_if_stale()
        # a rescan also re-applies the tags file (a meta bump); hold that
        # still so this pins the RUNS generation on its own
        s.meta_generation = m0
        after = _served(active, False, None)
        assert after["total"] == before["total"] + 1 == _cold(active, False, None)["total"]

    def test_a_tag_is_served(self, tmp_path):               # meta_generation
        root, s, active = self._setup(tmp_path)
        _served(active, True, None)
        s.add_tag(2, "keep")
        got = _served(active, True, None)
        assert got["total"] == 1 and got["all_tags"] == ["keep"]

    def test_a_rebuilt_store_of_the_same_folder_is_not_served_the_old_payload(self, tmp_path):
        root, s, active = self._setup(tmp_path)             # instance_seq
        _served(active, False, None)
        s2 = DatasetStore(root)
        s2.add_tag(1, "fresh")
        # same folder, a brand-new store whose generations happen to be equal
        s2.generation, s2.meta_generation = s.generation, s.meta_generation
        got = _served(_active([("k", s2)]), False, None)
        assert got["all_tags"] == ["fresh"]

    def test_the_date_tab_is_part_of_the_key(self, tmp_path):
        root, s, active = self._setup(tmp_path)
        _run(root, 7, A, date="2026-09-02")
        s._last_mtime = (0.0, -2)
        s.rescan_if_stale()
        all_rows = json.loads(_served(active, False, None)["rows_json"])
        day = json.loads(_served(active, False, "2026-09-02")["rows_json"])
        assert len(all_rows) == 4 and [r["id"] for r in day] == [7]

    def test_the_view_is_part_of_the_key(self, tmp_path):
        root, s, active = self._setup(tmp_path)
        s.add_tag(3, "t")
        assert _served(active, False, None)["total"] == 3
        assert _served(active, True, None)["total"] == 1

    def test_the_folder_path_and_key_are_part_of_the_key(self, tmp_path):
        root, s, active = self._setup(tmp_path)
        _served(active, False, None)
        relabeled = [dict(active[0], key="other")]
        rows = json.loads(_served(relabeled, False, None)["rows_json"])
        assert {r["f"] for r in rows} == {"other"}


def test_the_route_serves_the_memo_on_a_repeat(tmp_path):
    data = tmp_path / "data"
    for rid in range(1, 8):
        _run(data, rid, A, date="2026-09-01")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/workspace/add", data={"folder": str(data)}).status_code in (200, 302)
    r1 = c.get("/datasets")
    assert r1.status_code == 200
    n = R._DATASETS_PAYLOAD.computes
    r2 = c.get("/datasets")
    assert r2.status_code == 200 and R._DATASETS_PAYLOAD.computes == n
    assert R._DATASETS_PAYLOAD.hits >= 1
    # and a new run is on the very next page
    _run(data, 50, A, date="2026-09-01")
    _bump_dir(data / "2026-09-01")
    body = c.get("/datasets").get_data(as_text=True)
    assert '"id":50' in body and R._DATASETS_PAYLOAD.computes == n + 1
