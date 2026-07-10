"""Perf caches — bundle-input LRU (registry), node_meta reuse (contracts),
and the ndview byte-cache (serialized cubes + byte budget + byte-bound LRU).

All synthetic (tmp_path); no real archive needed.
"""
from __future__ import annotations

import json
import os
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

h5py = pytest.importorskip("h5py")
np = pytest.importorskip("numpy")

from quam_state_manager.core import ndview
from quam_state_manager.core.interactive_plots import contracts, h5reader, registry
from quam_state_manager.core.interactive_plots.recipes.base import Bundle


# ──────────────────────────────────────────────────────────────────────────
# Synthetic run folders
# ──────────────────────────────────────────────────────────────────────────


def _write_nc(path: Path, n_freq=40, seed=0, qubits=("q0", "q1")):
    """Minimal netCDF4-style ds file (dimension scales + DIMENSION_LIST)."""
    with h5py.File(path, "w") as f:
        q = f.create_dataset("qubit", data=np.array([s.encode() for s in qubits]))
        q.attrs["CLASS"] = np.bytes_("DIMENSION_SCALE")
        d = f.create_dataset("detuning", data=np.linspace(-5e6, 5e6, n_freq))
        d.attrs["CLASS"] = np.bytes_("DIMENSION_SCALE")
        data = np.random.default_rng(seed).normal(size=(len(qubits), n_freq))
        I = f.create_dataset("I", data=data)
        Q = f.create_dataset("Q", data=data * 0.5)
        for ds in (I, Q):
            ds.dims[0].attach_scale(q)
            ds.dims[1].attach_scale(d)
    return path


def _make_run_folder(root: Path, name="03_resonator_spectroscopy",
                     seed=0, f01=5.1e9) -> Path:
    folder = root / "2026-01-01" / f"#1_{name}_120000"
    folder.mkdir(parents=True)
    _write_nc(folder / "ds_raw.h5", seed=seed)
    (folder / "node.json").write_text(json.dumps({
        "metadata": {"name": name},
        "patches": [{"op": "replace", "path": "/qubits/q0/f_01",
                     "old": f01 - 1e6, "value": f01}],
    }))
    qs = folder / "quam_state"
    qs.mkdir()
    (qs / "state.json").write_text(json.dumps(
        {"qubits": {"q0": {"f_01": f01}}}))
    return folder


def _fake_run(folder: Path):
    return types.SimpleNamespace(folder_path=str(folder), experiment_name="x",
                                 fit_results={}, qubits=[], parameters={})


@pytest.fixture(autouse=True)
def _fresh_caches():
    with registry._bundle_cache_lock:
        registry._bundle_cache.clear()
    ndview._cache_clear()
    yield
    with registry._bundle_cache_lock:
        registry._bundle_cache.clear()
    ndview._cache_clear()


# ──────────────────────────────────────────────────────────────────────────
# #1 — Bundle-input cache
# ──────────────────────────────────────────────────────────────────────────


class TestBundleInputCache:
    def test_hit_returns_same_inputs_and_skips_disk(self, tmp_path, monkeypatch):
        folder = _make_run_folder(tmp_path)
        run = _fake_run(folder)
        first = registry._bundle_inputs(run)
        assert first["name"] == "03_resonator_spectroscopy"
        assert isinstance(first["raw"]["vars"]["I"], np.ndarray)
        assert first["quam_state"]["qubits"]["q0"]["f_01"] == 5.1e9

        calls = {"n": 0}
        real = h5reader.load_dataset

        def counting(*a, **k):
            calls["n"] += 1
            return real(*a, **k)

        monkeypatch.setattr(registry.h5reader, "load_dataset", counting)
        second = registry._bundle_inputs(run)
        assert second is first          # the cached entry, not a rebuild
        assert calls["n"] == 0          # zero dataset reads on a warm hit

    def test_cached_arrays_are_in_memory_numpy(self, tmp_path):
        # h5py-backed views would go stale after the file closes/changes —
        # the cache must hold materialized numpy only.
        folder = _make_run_folder(tmp_path)
        inputs = registry._bundle_inputs(_fake_run(folder))
        arr = inputs["raw"]["vars"]["I"]
        expected = arr.copy()
        os.remove(folder / "ds_raw.h5")   # cached data must survive the file
        assert np.array_equal(np.asarray(arr), expected)

    def test_mtime_bump_invalidates(self, tmp_path):
        folder = _make_run_folder(tmp_path)
        run = _fake_run(folder)
        first = registry._bundle_inputs(run)
        _write_nc(folder / "ds_raw.h5", seed=7)     # rewrite in place
        os.utime(folder / "ds_raw.h5", ns=(1, 10**18))  # force a distinct mtime
        second = registry._bundle_inputs(run)
        assert second is not first
        assert not np.array_equal(second["raw"]["vars"]["I"],
                                  first["raw"]["vars"]["I"])

    def test_input_file_appearing_invalidates(self, tmp_path):
        folder = _make_run_folder(tmp_path)
        run = _fake_run(folder)
        first = registry._bundle_inputs(run)
        assert first["fit"] is None
        _write_nc(folder / "ds_fit.h5", seed=3)
        second = registry._bundle_inputs(run)
        assert second is not first
        assert second["fit"] is not None

    def test_lru_bounded(self, tmp_path):
        for i in range(registry._BUNDLE_CACHE_MAX + 2):
            folder = _make_run_folder(tmp_path / f"w{i}", seed=i)
            registry._bundle_inputs(_fake_run(folder))
        assert len(registry._bundle_cache) == registry._BUNDLE_CACHE_MAX

    def test_thread_hammer_same_and_different_keys(self, tmp_path):
        folders = [_make_run_folder(tmp_path / f"w{i}", seed=i, f01=5e9 + i)
                   for i in range(3)]
        runs = [_fake_run(f) for f in folders]
        errors: list[Exception] = []
        barrier = threading.Barrier(12)

        def worker(i):
            barrier.wait()
            try:
                for j in range(20):
                    run = runs[(i + j) % len(runs)]
                    inputs = registry._bundle_inputs(run)
                    # every result must be internally consistent with its run
                    idx = runs.index(run)
                    assert inputs["quam_state"]["qubits"]["q0"]["f_01"] == 5e9 + idx
                    assert inputs["raw"]["vars"]["I"].shape == (2, 40)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=12) as ex:
            list(ex.map(worker, range(12)))
        assert errors == []
        assert len(registry._bundle_cache) <= registry._BUNDLE_CACHE_MAX

    def test_build_figure_warm_equals_cold(self, tmp_path):
        # End-to-end through a real recipe: warm build (cache hit) must return
        # an identical payload to the cold build.
        folder = _make_run_folder(tmp_path)
        run = _fake_run(folder)
        run.experiment_name = "03_resonator_spectroscopy"
        from quam_state_manager.core.interactive_plots import (
            build_interactive_figure, list_interactive_figures)
        menu = [m for m in list_interactive_figures(run) if m["available"]]
        if not menu:
            pytest.skip("synthetic run produced no available figure")
        key = menu[0]["key"]
        cold = build_interactive_figure(run, key)
        assert cold is not None
        warm = build_interactive_figure(run, key)
        assert json.dumps(warm, sort_keys=True) == json.dumps(cold, sort_keys=True)


# ──────────────────────────────────────────────────────────────────────────
# #2 — pre_update_value uses Bundle.node_meta (disk only as fallback)
# ──────────────────────────────────────────────────────────────────────────


class TestPreUpdateValueNodeMeta:
    def _bundle(self, folder, node_meta):
        return Bundle(run=_fake_run(folder), node_meta=node_meta,
                      quam_state={"qubits": {"q0": {"f_01": 5.1e9}}})

    def test_node_meta_patches_no_disk_read(self, tmp_path, monkeypatch):
        folder = _make_run_folder(tmp_path)
        bundle = self._bundle(folder, {
            "metadata": {"name": "x"},
            "patches": [{"op": "replace", "path": "/qubits/q0/f_01",
                         "old": 4.9e9, "value": 5.1e9}]})

        def boom(*a, **k):
            raise AssertionError("disk read despite node_meta present")

        monkeypatch.setattr(contracts.safe_io, "read_json", boom)
        v, src = contracts.pre_update_value(
            bundle, ["/qubits/q0/f_01"], "qubits.q0.f_01")
        assert (v, src) == (4.9e9, "patches")

    def test_empty_node_meta_falls_back_to_disk(self, tmp_path, monkeypatch):
        folder = _make_run_folder(tmp_path, f01=5.1e9)   # node.json old=5.0999e9
        bundle = self._bundle(folder, {})
        calls = {"n": 0}
        real = contracts.safe_io.read_json

        def counting(path, *a, **k):
            calls["n"] += 1
            return real(path, *a, **k)

        monkeypatch.setattr(contracts.safe_io, "read_json", counting)
        v, src = contracts.pre_update_value(
            bundle, ["/qubits/q0/f_01"], "qubits.q0.f_01")
        assert src == "patches" and v == 5.1e9 - 1e6
        assert calls["n"] == 1

    def test_no_matching_patch_returns_snapshot(self, tmp_path):
        folder = _make_run_folder(tmp_path)
        bundle = self._bundle(folder, {"metadata": {"name": "x"},
                                       "patches": None})
        v, src = contracts.pre_update_value(
            bundle, ["/qubits/q0/f_01"], "qubits.q0.f_01")
        assert (v, src) == (5.1e9, "snapshot")

    def test_matching_patch_without_old_stays_unrecoverable(self, tmp_path):
        bundle = self._bundle(_make_run_folder(tmp_path), {
            "metadata": {"name": "x"},
            "patches": [{"op": "add", "path": "/qubits/q0/f_01",
                         "value": 5.1e9}]})
        assert contracts.pre_update_value(
            bundle, ["/qubits/q0/f_01"], "qubits.q0.f_01") == (None, "")


# ──────────────────────────────────────────────────────────────────────────
# #4 — ndview byte cache
# ──────────────────────────────────────────────────────────────────────────


def _write_line(path: Path, n: int, dip_at: int | None = None, seed=0):
    """1-sweep-dim file whose float noise serializes fat (~19 B/element)."""
    with h5py.File(path, "w") as f:
        d = f.create_dataset("detuning", data=np.linspace(0.0, 1.0, n))
        d.attrs["CLASS"] = np.bytes_("DIMENSION_SCALE")
        y = np.random.default_rng(seed).normal(size=n)
        if dip_at is not None:
            y[dip_at] = -100.0
        I = f.create_dataset("I", data=y)
        I.dims[0].attach_scale(d)
    return path


class TestCubeByteCache:
    def test_hit_identical_bytes_and_rebuild_matches(self, tmp_path):
        p = _write_nc(tmp_path / "ds_raw.h5")
        b1, m1 = ndview.build_cube_bytes(p, "I")
        b2, m2 = ndview.build_cube_bytes(p, "I")
        assert b2 is b1                    # warm hit: the cached bytes object
        assert m2 == m1 and m1["ok"] is True
        ndview._cache_clear()
        b3, _ = ndview.build_cube_bytes(p, "I")
        assert b3 == b1                    # deterministic rebuild, byte-for-byte
        cube = json.loads(b1)
        assert cube["ok"] and [d["name"] for d in cube["dims"]] == ["qubit", "detuning"]

    def test_build_cube_dict_view_matches_bytes(self, tmp_path):
        p = _write_nc(tmp_path / "ds_raw.h5")
        raw, _ = ndview.build_cube_bytes(p, "I")
        assert ndview.build_cube(p, "I") == json.loads(raw)

    def test_mtime_bump_invalidates(self, tmp_path):
        p = _write_nc(tmp_path / "ds_raw.h5", seed=0)
        b1, _ = ndview.build_cube_bytes(p, "I")
        _write_nc(p, seed=9)
        os.utime(p, ns=(1, 10**18))
        b2, _ = ndview.build_cube_bytes(p, "I")
        assert b2 != b1
        assert json.loads(b2)["data"] != json.loads(b1)["data"]

    def test_byte_budget_enforced_with_peak_preserved(self, tmp_path):
        # 420k elements < the 500k ELEMENT budget (no first-pass decimation)
        # but ~8.5 MB of JSON — exactly the measured 6.5 MB-cube blind spot.
        n = 420_000
        p = _write_line(tmp_path / "ds_raw.h5", n, dip_at=n // 3)
        raw, meta = ndview.build_cube_bytes(p, "I")
        assert meta["ok"]
        assert len(raw) <= ndview._CUBE_BYTE_TARGET + 256 * 1024   # ≤ ~4 MB ships
        cube = json.loads(raw)
        det = next(d for d in cube["dims"] if d["name"] == "detuning")
        assert det["decimated"]
        assert (n // 3) in cube["kept"]["detuning"]   # the dip survived
        assert min(v for v in cube["data"] if v is not None) == -100.0
        # kept indices still map to true source coords (click-snap contract)
        idx = cube["kept"]["detuning"].index(n // 3)
        assert cube["dims"][0]["coord"][idx] == pytest.approx((n // 3) / (n - 1))

    def test_small_cube_unaffected_by_byte_pass(self, tmp_path):
        p = _write_nc(tmp_path / "ds_raw.h5")
        cube = ndview.build_cube(p, "I")
        assert cube["ok"]
        assert not any(d["decimated"] for d in cube["dims"])

    def test_lru_bounded_by_bytes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ndview, "_CUBE_CACHE_MAX_BYTES", 120_000)
        paths = []
        for i in range(4):
            p = _write_line(tmp_path / f"f{i}.h5", 3_000, seed=i)  # ~60 KB each
            paths.append(p)
            ndview.build_cube_bytes(p, "I")
        assert ndview._cube_cache_total <= 120_000
        assert len(ndview._cube_cache) < 4                 # some were evicted
        # newest entry survived
        key = (str(paths[-1]), paths[-1].stat().st_mtime_ns, "I")
        assert key in ndview._cube_cache

    def test_lru_bounded_by_entry_count(self, tmp_path):
        for i in range(ndview._CUBE_CACHE_MAX + 3):
            p = _write_line(tmp_path / f"g{i}.h5", 50, seed=i)
            ndview.build_cube_bytes(p, "I")
        assert len(ndview._cube_cache) == ndview._CUBE_CACHE_MAX
        assert ndview._cube_cache_total > 0

    def test_error_paths_still_classified(self, tmp_path):
        raw, meta = ndview.build_cube_bytes(tmp_path / "absent.h5", "x")
        cube = json.loads(raw)
        assert meta["ok"] is False and cube["ok"] is False
        p = _write_nc(tmp_path / "ds_raw.h5")
        raw2, meta2 = ndview.build_cube_bytes(p, "__nope__")
        assert meta2["ok"] is False and "No variable" in json.loads(raw2)["error"]


# ──────────────────────────────────────────────────────────────────────────
# Route splice — /ndview/data composes cached bytes + per-request extras
# ──────────────────────────────────────────────────────────────────────────


class TestNdviewRouteSplice:
    def test_payload_shape_unchanged(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore
        from quam_state_manager.web.app import create_app
        from quam_state_manager.web import routes as _r
        folder = _make_run_folder(tmp_path)
        store = DatasetStore(tmp_path)
        run_ids = list(store.runs)
        assert run_ids, "synthetic run folder was not scanned"
        app = create_app()
        app.config["dataset_store"] = store
        client = app.test_client()
        uid = _r._dataset_uid(_r._folder_key(store.folder_path), run_ids[0])
        r = client.get(f"/dataset/{uid}/ndview/data",
                       query_string={"which": "ds_raw.h5", "var": "I"})
        assert r.status_code == 200
        assert r.mimetype == "application/json"
        payload = json.loads(r.data)          # valid JSON after the byte splice
        # flat shape: cube keys AND per-request extras at the top level
        assert payload["ok"] is True
        assert payload["uid"] == uid and payload["which"] == "ds_raw.h5"
        assert "click" in payload and "candidates" in payload["click"]
        assert [d["name"] for d in payload["dims"]] == ["qubit", "detuning"]
        # warm request → byte-identical body (cache hit + fresh splice)
        r2 = client.get(f"/dataset/{uid}/ndview/data",
                        query_string={"which": "ds_raw.h5", "var": "I"})
        assert r2.data == r.data

    def test_error_cube_still_flat_json(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore
        from quam_state_manager.web.app import create_app
        from quam_state_manager.web import routes as _r
        _make_run_folder(tmp_path)
        store = DatasetStore(tmp_path)
        app = create_app()
        app.config["dataset_store"] = store
        client = app.test_client()
        uid = _r._dataset_uid(_r._folder_key(store.folder_path), list(store.runs)[0])
        r = client.get(f"/dataset/{uid}/ndview/data",
                       query_string={"which": "ds_raw.h5", "var": "__nope__"})
        assert r.status_code == 200
        payload = json.loads(r.data)
        assert payload["ok"] is False and payload["uid"] == uid
