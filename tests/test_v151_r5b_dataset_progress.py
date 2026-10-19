"""R3B MINOR 8: the Datasets page says only what is true.

* The "wrote state" mark, its chip and its search help promised that such a
  run's fits "can be applied" -- false for runs whose reproduced figures still
  refuse clicks. They now say what the run did: it wrote N values into the
  chip state.
* The one-time index rebuild after an upgrade that changed the index format
  (spread over many polls on a big archive) only said "still indexing". It
  now says "Re-indexing after upgrade: N of M runs", counted in memory from
  the old index read once at startup -- no extra disk read per poll.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import dataset as D, hub_sync
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]


def _run(root, rid):
    p = root / "2026-10-18" / f"#{rid}_scan_010000"
    p.mkdir(parents=True)
    (p / "node.json").write_text(json.dumps({"id": rid, "parents": [],
        "metadata": {"name": "scan", "status": "finished"},
        "data": {"parameters": {"model": {"qubits": ["q1"]}}},
        "patches": [{"op": "replace", "path": "/quam/qubits/q1/f_01", "value": 5e9}]}), encoding="utf-8")
    (p / "data.json").write_text("{}", encoding="utf-8")


def test_upgrade_progress_advances_across_polls_without_reading_the_cache_again(tmp_path, monkeypatch):
    root, cache = tmp_path / "data", tmp_path / "cache"
    for rid in range(1, 17):
        _run(root, rid)
    cache.mkdir()
    first = D.DatasetStore(root, cache_dir=cache)
    assert first.flush_store_cache()
    cp = first._store_cache_path()
    raw = json.loads(cp.read_text(encoding="utf-8"))
    raw["v"] = 3
    cp.write_text(json.dumps(raw), encoding="utf-8")
    with D.yielding_scan(lambda: True, chunk=2):
        store = D.DatasetStore(root, cache_dir=cache)
    assert store.run_count == 2 and not store.index_complete
    assert getattr(store, "reindex_progress", None) == {"done": 2, "total": 16}
    # The old cache has already been read. A progress read must use memory.
    real_read = Path.read_text
    def read(path, *args, **kw):
        assert path != cp, "progress re-read the persisted index"
        return real_read(path, *args, **kw)
    monkeypatch.setattr(Path, "read_text", read)
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    client = app.test_client()
    assert client.post("/workspace/add", data={"folder": str(root)}).status_code == 200
    with app.app_context():
        routes._dataset_store_lru()[root] = store
    monkeypatch.setattr(routes, "_kick_dataset_index_warmer", lambda: None)
    monkeypatch.setattr(routes, "_enqueue_exp_ingest", lambda *a, **k: None)
    monkeypatch.setattr(hub_sync, "catching_up", lambda: True)
    page = client.get("/datasets", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert "Re-indexing after upgrade: 2 of 16 runs" in page
    got = client.get("/datasets/changes-since?ts=0").get_json()
    assert got["reindex"] == {"done": 2, "total": 16} and got["partial"]
    monkeypatch.setattr(hub_sync, "catching_up", lambda: False)
    with D.yielding_scan(lambda: True, chunk=2):
        store.changes_since(0)
    assert store.reindex_progress == {"done": 4, "total": 16}
    monkeypatch.setattr(hub_sync, "catching_up", lambda: True)
    got = client.get("/datasets/changes-since?ts=0").get_json()
    assert got["reindex"] == {"done": 4, "total": 16}
    monkeypatch.setattr(hub_sync, "catching_up", lambda: False)
    got = client.get("/datasets/changes-since?ts=0").get_json()
    assert not got["partial"] and "reindex" not in got and store.run_count == 16
    store.flush_store_cache()


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_wrote_mark_and_progress_in_the_real_dataset_script():
    p = subprocess.run(["node", str(ROOT / "tests/v151_dataset_progress_selfcheck.cjs")],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120)
    if p.returncode == 2 and "jsdom not installed" in p.stderr:
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, p.stdout + p.stderr


def test_multi_folder_progress_keeps_the_total_until_all_folders_finish():
    from types import SimpleNamespace
    helper = getattr(routes, "_dataset_reindex_progress", None)
    assert callable(helper), "the render and poll need the same in-memory progress"
    active = [{"store": SimpleNamespace(reindex_progress={"done": 8, "total": 8})},
              {"store": SimpleNamespace(reindex_progress={"done": 2, "total": 16})}]
    assert helper(active) == {"done": 10, "total": 24}
    active[1]["store"].reindex_progress = {"done": 16, "total": 16}
    assert helper(active) is None


def test_the_wrote_state_chip_and_help_promise_no_fit_apply(tmp_path, monkeypatch):
    root = tmp_path / "data"
    _run(root, 1)
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    client = app.test_client()
    assert client.post("/workspace/add", data={"folder": str(root)}).status_code == 200
    monkeypatch.setattr(routes, "_kick_dataset_index_warmer", lambda: None)
    monkeypatch.setattr(routes, "_enqueue_exp_ingest", lambda *a, **k: None)
    page = client.get("/datasets", headers={"HX-Request": "true"}).get_data(as_text=True)
    chip = re.search(r'<button[^>]*\bds-wrote-chip\b[^>]*\btitle="([^"]*)"', page)
    hint = re.search(r'data-example="is:wrote">is:wrote</button></td>\s*<td>(.*?)</td>', page, re.S)
    assert chip and hint, "the wrote-state chip or its search help is gone"
    for text in (chip.group(1), hint.group(1)):
        assert "wrote values into the chip state" in text, text
        assert not re.search(r"\bfits?\b|\bappl", text, re.I), "promises a fit can be applied: " + text
