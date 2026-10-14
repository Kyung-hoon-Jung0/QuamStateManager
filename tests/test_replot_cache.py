"""Name-free pins for replay source invalidation and cache limits."""
import json
import os
from types import SimpleNamespace

from quam_state_manager.core.interactive_plots import replot, replot_cache as cache
from quam_state_manager.generator.iplot_sources import fingerprint


def _result(source):
    return {"figures": [{"key": "fit", "axes": []}], "errors": [],
            "module_files": [{"path": str(source)}], "module_trees": [],
            "code_fingerprint": fingerprint([source])}


def test_disk_and_ram_hit_invalidate_after_analysis_edit(tmp_path, monkeypatch):
    source = tmp_path / "plotting.py"
    source.write_text("value=1")
    run = SimpleNamespace(folder_path=str(tmp_path / "run"))
    calls = []
    monkeypatch.setattr("quam_state_manager.core.config_generator.get_selected_env", lambda _: "env")
    monkeypatch.setattr(replot, "_invoke", lambda *a: calls.append(1) or _result(source))
    replot._CACHE.clear()
    first = replot.replot_run(run, tmp_path / "instance")
    second = replot.replot_run(run, tmp_path / "instance")
    # v1.3.0: cached response identity -> cached artists plus fresh P3 proof wrappers.
    assert second == first and len(calls) == 1
    assert second is not first and second["figures"][0] is not first["figures"][0]
    assert second["figures"][0]["axes"] is first["figures"][0]["axes"]
    replot._CACHE.clear()
    assert replot.replot_run(run, tmp_path / "instance")["cache_key"] == first["cache_key"]
    assert len(calls) == 1
    stat = source.stat()
    # a touch without an edit (new mtime, same bytes) re-hashes and still hits
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    replot._CACHE.clear()
    assert replot.replot_run(run, tmp_path / "instance")["cache_key"] == first["cache_key"]
    assert len(calls) == 1
    # an edit (stat-identical edits are out of scope: unchanged stats skip the hash)
    source.write_text("value=22")
    changed = replot.replot_run(run, tmp_path / "instance")
    assert len(calls) == 2 and changed["cache_key"] != first["cache_key"]
    assert changed["code_fingerprint"] != first["code_fingerprint"]


def test_unchanged_sources_hit_without_hashing(tmp_path, monkeypatch):
    source = tmp_path / "plotting.py"
    source.write_text("value=1")
    run = SimpleNamespace(folder_path=str(tmp_path / "run"))
    monkeypatch.setattr("quam_state_manager.core.config_generator.get_selected_env", lambda _: "env")
    monkeypatch.setattr(replot, "_invoke", lambda *a: _result(source))
    replot._CACHE.clear()
    first = replot.replot_run(run, tmp_path / "instance")
    hashed = []
    real = cache.fingerprint
    monkeypatch.setattr(cache, "fingerprint", lambda *a: hashed.append(1) or real(*a))
    replot._CACHE.clear()
    assert replot.replot_run(run, tmp_path / "instance")["cache_key"] == first["cache_key"]
    assert replot.replot_run(run, tmp_path / "instance")["cache_key"] == first["cache_key"]
    assert hashed == []
    (tmp_path / "added.py").write_text("")
    meta = {"context": {}, "files": [str(source)], "trees": [str(tmp_path)], "code_fingerprint": "x",
            "stats": cache.snapshot([str(source)], [])}
    assert cache.valid(meta, {}) is False and hashed == [1]


def test_cache_watches_current_code_for_older_rungs(tmp_path):
    current = tmp_path / "current.py"
    pinned = tmp_path / "pinned.py"
    current.write_text("x=1")
    pinned.write_text("x=0")
    result = {"figures": [], "saved_figures": [{"key": "fit"}], "errors": [],
              "module_files": [{"path": str(pinned)}], "module_trees": [],
              "code_fingerprint": fingerprint([pinned]),
              "cache_watch": {"files": [str(current)], "trees": [],
                              "code_fingerprint": fingerprint([current])}}
    disk = cache.DiskCache(tmp_path / "instance")
    assert disk.store({"n": 1}, result) is not None
    assert disk.load({"n": 1}) is not None
    current.write_text("x=22")
    assert disk.load({"n": 1}) is None


def test_ram_hits_update_recency(tmp_path, monkeypatch):
    monkeypatch.setattr(replot, "_CACHE_MAX", 2)
    monkeypatch.setattr("quam_state_manager.core.config_generator.get_selected_env", lambda _: "env")
    source = tmp_path / "plot.py"
    source.write_text("value=1")
    monkeypatch.setattr(replot, "_invoke", lambda *a: _result(source))
    replot._CACHE.clear()
    runs = [SimpleNamespace(folder_path=str(tmp_path / str(i))) for i in range(3)]
    for r in runs[:2]:
        replot.replot_run(r, tmp_path / "instance")
    replot.replot_run(runs[0], tmp_path / "instance")
    replot.replot_run(runs[2], tmp_path / "instance")
    assert list(replot._CACHE) == [str(tmp_path / "0"), str(tmp_path / "2")]


def test_cache_inputs_context_and_schema_are_part_of_key(tmp_path, monkeypatch):
    (tmp_path / "quam_state").mkdir()
    files = [tmp_path / n for n in ("node.json", "data.json", "ds_raw.h5", "ds_fit.h5", "quam_state/state.json")]
    for f in files:
        f.write_text("{}"); before = replot._fingerprint(tmp_path)
        f.write_text("{ }")
        assert replot._fingerprint(tmp_path) != before
    source = tmp_path / "plot.py"
    source.write_text("x=1")
    run = SimpleNamespace(folder_path=str(tmp_path))
    calls = []
    selected = ["env1"]
    monkeypatch.setattr("quam_state_manager.core.config_generator.get_selected_env", lambda _: selected[0])
    monkeypatch.setattr(replot, "_invoke", lambda *a: calls.append(1) or _result(source))
    replot._CACHE.clear()
    keys = [replot.replot_run(run, tmp_path / "instance")["cache_key"]]
    selected[0] = "env2"
    keys.append(replot.replot_run(run, tmp_path / "instance")["cache_key"])
    keys.append(replot.replot_run(run, tmp_path / "instance", source_root=str(tmp_path))["cache_key"])
    monkeypatch.setattr(cache, "SCHEMA", "next")
    keys.append(replot.replot_run(run, tmp_path / "instance")["cache_key"])
    # v1.4.3: an upgrade of SM's own replay code (same run, same lab code) is a new key too.
    monkeypatch.setattr(replot, "_replay_code_digest", lambda: "other code")
    keys.append(replot.replot_run(run, tmp_path / "instance")["cache_key"])
    assert len(calls) == len(set(keys)) == 5


def test_replay_code_digest_follows_the_worker_scripts(tmp_path, monkeypatch):
    """Editing any script the lab worker runs changes the digest the cache key carries."""
    from quam_state_manager.core import config_generator
    for name in replot._REPLAY_CODE:
        (tmp_path / name).write_text("# " + name)
    monkeypatch.setattr(config_generator, "_script_path", lambda name: tmp_path / name)
    digests = []
    for name in replot._REPLAY_CODE:
        monkeypatch.setattr(replot, "_replay_code_digest_value", None)
        digests.append(replot._replay_code_digest())
        (tmp_path / name).write_text("# edited " + name)
    monkeypatch.setattr(replot, "_replay_code_digest_value", None)
    digests.append(replot._replay_code_digest())
    assert len(set(digests)) == len(digests)
    assert replot._replay_code_digest() == digests[-1]  # computed once per process


def test_package_membership_and_disk_corruption(tmp_path):
    tree = tmp_path / "util"
    tree.mkdir()
    source = tree / "plot.py"
    source.write_text("x=1")
    result = _result(source)
    result.update(module_trees=[str(tree)], code_fingerprint=fingerprint([source], [tree]))
    disk = cache.DiskCache(tmp_path / "instance")
    disk.store({}, result)
    assert disk.load({})
    (tree / "new.py").write_text("x=2")
    assert disk.load({}) is None
    result["code_fingerprint"] = fingerprint([source], [tree])
    disk.store({}, result)
    (disk.root / (result["cache_key"] + ".json.gz")).write_bytes(b"broken")
    assert disk.load({}) is None


def test_lru_count_byte_and_disk_eviction(tmp_path, monkeypatch):
    replot._CACHE.clear()
    monkeypatch.setattr(replot, "_CACHE_MAX", 2)
    replot._remember("a", {}, {}, 3)
    replot._remember("b", {}, {}, 3)
    replot._CACHE.move_to_end("a")
    replot._remember("c", {}, {}, 3)
    assert list(replot._CACHE) == ["a", "c"]
    monkeypatch.setattr(cache, "RAM_BYTES", 4)
    replot._remember("d", {}, {}, 3)
    assert list(replot._CACHE) == ["d"]
    source = tmp_path / "plot.py"
    source.write_text("x=1")
    monkeypatch.setattr(cache, "RAM_BYTES", 4096)
    disk = cache.DiskCache(tmp_path)
    disk.store({"n": 1}, _result(source))
    disk.store({"n": 2}, _result(source))
    paths = list(disk.root.glob("*.json.gz"))
    os.utime(paths[0], ns=(1, 1))
    monkeypatch.setattr(cache, "DISK_BYTES", max(p.stat().st_size for p in paths))
    disk.evict()
    assert not paths[0].exists() and paths[1].exists()
    assert len(list(disk.root.glob("*.index.json"))) == 1


def test_source_manifest_and_edit_during_replay(tmp_path, monkeypatch):
    import sys
    from types import ModuleType
    from quam_state_manager.generator import run_interactive_replot as runner
    root = tmp_path / "sources"
    pkg = root / "calibration_utils"
    pkg.mkdir(parents=True)
    source = pkg / "__init__.py"
    source.write_text("")
    helper = root / "helper.py"
    helper.write_text("value=1")
    module = ModuleType("calibration_utils")
    module.__file__, module.__path__ = str(source), [str(pkg)]
    extra = ModuleType("helper")
    extra.__file__ = str(helper)
    monkeypatch.setitem(sys.modules, "calibration_utils", module)
    monkeypatch.setitem(sys.modules, "helper", extra)
    monkeypatch.syspath_prepend(str(__import__("pathlib").Path(runner.__file__).parent))
    manifest = runner._source_manifest(SimpleNamespace(source_root=str(root)), "01_node")
    assert str(helper.resolve()) in [m["path"] for m in manifest["module_files"]]
    result = _result(helper)
    helper.write_text("value=2")
    disk = cache.DiskCache(tmp_path / "instance")
    assert disk.store({}, result) is None
    assert disk.load({}) is None
