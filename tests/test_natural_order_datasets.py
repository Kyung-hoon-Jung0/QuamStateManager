"""Natural ordering across the DATASETS area (customer rule, 2026-09-09).

The report was a live-diff review listing ``…weights_imag.1009`` above
``…weights_imag.101``: a LEXICOGRAPHIC sort of a path whose last segment is a
number. The customer's rule is product-wide — "SM 전반에서 순서에 관한 것은
반드시 이렇게 카운트되어야 한다": 101 < 1009 < 1010 < 1011, and q2 < q10.

``differ.py`` was fixed under that report (df50076). This file pins the same
rule for the datasets area — the run list, the sidebar tree, the N-D viewer,
the compare hub, and the two diff row builders — and every case here DRIVES the
real producer (a ``DatasetStore`` over real folders, a real scanned workspace,
a real HDF5 file, a real ``ComparisonSnapshot``, the real row builders) rather
than asserting on ``natural_key`` itself, which would prove nothing about the
call site.

Every assertion below FAILS if its one sort is reverted to the bare string
form; each was mutation-checked when written.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import json_diff
from quam_state_manager.core.dataset import DatasetStore, build_trend_data


# ---------------------------------------------------------------------------
# real run folders — the shape a qualibrate archive actually has
# ---------------------------------------------------------------------------

def _write_run(root: Path, run_id: int, *, name: str, date: str = "2026-09-09",
               qubits: list[str] | None = None,
               fit: dict | None = None, hhmmss: str = "010000") -> Path:
    """One complete run folder: ``<root>/<date>/#<id>_<name>_<HHMMSS>``."""
    qubits = qubits if qubits is not None else ["q1"]
    run = root / date / f"#{run_id}_{name}_{hhmmss}"
    run.mkdir(parents=True, exist_ok=True)
    node = {
        "metadata": {"name": name, "status": "successful",
                     "run_start": f"{date}T01:00:00",
                     "run_end": f"{date}T01:00:01", "description": name},
        "data": {"parameters": {"model": {"qubits": qubits}}, "outcomes": {}},
        "id": run_id, "parents": [], "created_at": f"{date}T01:00:00",
    }
    (run / "node.json").write_text(json.dumps(node), encoding="utf-8")
    if fit is not None:
        (run / "data.json").write_text(json.dumps({"fit_results": fit}),
                                       encoding="utf-8")
    return run


class TestDatasetStoreOrder:
    """``core/dataset.py`` — the lists the run table and its filters render."""

    def test_experiment_type_filter_counts_the_node_number(self, tmp_path):
        """Node names carry a numeric prefix. ``9_…`` is the ninth node, not
        the last one — a string sort put it after ``15h_…`` and ``10_…``."""
        root = tmp_path / "ws"
        for i, name in enumerate(["2_close_other_qms", "9_qubit_spectroscopy",
                                  "10_power_rabi", "15h_ramsey"]):
            _write_run(root, 100 + i, name=name)
        store = DatasetStore(root)
        assert store.experiment_types == [
            "2_close_other_qms", "9_qubit_spectroscopy",
            "10_power_rabi", "15h_ramsey"]

    def test_a_truncated_walk_merges_in_the_same_order(self, tmp_path):
        """The deadline branch MERGES the walked types into the ones already
        held and re-sorts — its own sort, and the one the customer sees while
        a big archive is still indexing."""
        import time as _time

        root = tmp_path / "ws"
        for i, name in enumerate(["2_a", "9_b", "10_c"]):
            _write_run(root, 150 + i, name=name)
        store = DatasetStore(root)
        assert store.force_rescan(deadline=_time.monotonic() - 1.0) is True
        assert store.experiment_types == ["2_a", "9_b", "10_c"]

    def test_a_restored_session_lists_the_same_order(self, tmp_path,
                                                     monkeypatch):
        """docs/171: a second process rehydrates the run table from the store
        cache. Under a truncated verification walk that rehydrated list IS
        what the table shows."""
        from quam_state_manager.core import dataset as ds

        root = tmp_path / "ws"
        cache = tmp_path / "inst" / "workspace_cache"
        cache.parent.mkdir(parents=True)
        for i, name in enumerate(["2_a", "9_b", "10_c"]):
            _write_run(root, 170 + i, name=name)
        first = DatasetStore(root, cache_dir=cache)
        assert first.flush_store_cache() is True
        monkeypatch.setattr(ds, "_COLD_SCAN_BUDGET_S", -1.0)
        second = DatasetStore(root, cache_dir=cache)
        assert second.cache_hit_runs == 3
        assert second.experiment_types == ["2_a", "9_b", "10_c"]

    def test_the_experiment_column_sorts_by_the_node_number(self, tmp_path):
        root = tmp_path / "ws"
        for i, name in enumerate(["2_a", "9_b", "10_c"]):
            _write_run(root, 200 + i, name=name)
        store = DatasetStore(root)
        rows = store.list_runs(sort="experiment", desc=False)
        assert [r["experiment_name"] for r in rows] == ["2_a", "9_b", "10_c"]
        # descending is the same comparison reversed — not a second rule.
        rows_d = store.list_runs(sort="experiment", desc=True)
        assert [r["experiment_name"] for r in rows_d] == ["10_c", "9_b", "2_a"]

    def test_the_qubit_summary_counts_q2_before_q10(self, tmp_path):
        root = tmp_path / "ws"
        _write_run(root, 300, name="t1", qubits=["q2", "q10", "q1"])
        store = DatasetStore(root)
        assert store.summary_stats["unique_qubits"] == ["q1", "q2", "q10"]

    def test_the_tag_chips_count(self, tmp_path):
        root = tmp_path / "ws"
        _write_run(root, 400, name="t1")
        _write_run(root, 401, name="t1", hhmmss="010001")
        store = DatasetStore(root)
        store.add_tag(400, "batch10")
        store.add_tag(400, "batch2")
        store.add_tag(401, "batch9")
        assert store.list_all_tags() == ["batch2", "batch9", "batch10"]

    def test_the_metric_column_reads_the_FIRST_qubit(self, tmp_path):
        """``_extract_key_metric`` shows "the first qubit's" number. Of
        {q2, q10} a string sort calls q10 first, so the table put q10's value
        under a column every reader takes for the lowest qubit."""
        root = tmp_path / "ws"
        _write_run(root, 500, name="04_power_rabi", qubits=["q2", "q10"],
                   fit={"q10": {"amplitude": 0.9}, "q2": {"amplitude": 0.1}})
        store = DatasetStore(root)
        row = store.list_runs()[0]
        assert "0.1" in row["key_metric"], row["key_metric"]

    def test_the_sort_banner_scalar_reads_the_FIRST_qubit(self, tmp_path):
        """Same rule, the other consumer: ``sort_scalars``' ``first`` value."""
        root = tmp_path / "ws"
        _write_run(root, 600, name="04_power_rabi", qubits=["q2", "q10"],
                   fit={"q10": {"amplitude": 0.9}, "q2": {"amplitude": 0.1}})
        store = DatasetStore(root)
        amp = store.runs[600].sort_scalars["amplitude"]
        first = amp[0] if isinstance(amp, list) else amp
        assert first == pytest.approx(0.1)

    def test_trend_series_are_ordered_by_qubit_number(self, tmp_path):
        root = tmp_path / "ws"
        _write_run(root, 700, name="04_power_rabi", qubits=["q2", "q10"],
                   fit={"q10": {"amplitude": 0.9}, "q2": {"amplitude": 0.1}})
        store = DatasetStore(root)
        trend = build_trend_data(list(store.runs.values()))
        qubits = [s["qubit"] for s in trend["series"]]
        assert qubits == ["q2", "q10"]

    def test_the_stores_trend_payload_orders_qubits_too(self, tmp_path):
        root = tmp_path / "ws"
        _write_run(root, 800, name="04_power_rabi", qubits=["q2", "q10"],
                   fit={"q10": {"amplitude": 0.9}, "q2": {"amplitude": 0.1}})
        store = DatasetStore(root)
        trend = store.get_trend_data("04_power_rabi")
        assert [s["qubit"] for s in trend["series"]] == ["q2", "q10"]


class TestSidebarTreeOrder:
    """``core/scanner.py`` — ``build_nested_tree`` IS the sidebar's model."""

    def _tree(self, tmp_path, dirs):
        from quam_state_manager.core.scanner import build_nested_tree, _scan_root

        root = tmp_path / "ws"
        for i, d in enumerate(dirs):
            exp = root / d / "2026-02-19" / f"#{i + 1}_ramsey_010000"
            state = exp / "quam_state"
            state.mkdir(parents=True)
            (state / "state.json").write_text(
                json.dumps({"qubits": {}, "__class__": "Quam"}), encoding="utf-8")
            (state / "wiring.json").write_text(
                json.dumps({"wiring": {}, "network": {}}), encoding="utf-8")
            (exp / "node.json").write_text(json.dumps({
                "created_at": "2026-02-19T01:00:00+09:00",
                "metadata": {"name": "ramsey", "status": "finished",
                             "run_start": "2026-02-19T01:00:00+09:00",
                             "run_end": "2026-02-19T01:00:00+09:00"},
                "data": {"parameters": {"model": {"qubits": []}},
                         "outcomes": {}, "quam": "./quam_state"},
                "id": i + 1, "parents": [],
            }), encoding="utf-8")
        return build_nested_tree(root, _scan_root(root))

    def test_folder_levels_count(self, tmp_path):
        """A real folder level is a directory name a person reads."""
        nodes = self._tree(tmp_path, ["chip10", "chip2", "chip1"])
        assert [n["name"] for n in nodes] == ["chip1", "chip2", "chip10"]

    def test_a_suffixed_date_level_counts_too(self, tmp_path):
        """``is_date`` is a SEARCH, so a suffixed date dir lands in the DATE
        bucket — where a string sort puts _batch2 after _batch10."""
        nodes = self._tree(tmp_path,
                           ["2026-02-19_batch2", "2026-02-19_batch10"])
        # date levels render newest-first, so natural DESC is batch10 first
        assert [n["name"] for n in nodes] == [
            "2026-02-19_batch10", "2026-02-19_batch2"]


class TestNdviewOrder:
    """``core/ndview.py`` — the Data tab's file picker and variable cards."""

    def test_the_h5_file_picker_counts(self, tmp_path):
        from quam_state_manager.core import ndview

        run = tmp_path / "#1_run_010000"
        run.mkdir()
        for n in ["ds_proc_10.h5", "ds_proc_2.h5", "ds_raw.h5"]:
            (run / n).write_bytes(b"")
        assert ndview.list_h5_files(run) == [
            "ds_proc_2.h5", "ds_proc_10.h5", "ds_raw.h5"]

    def test_the_variable_cards_count(self, tmp_path):
        """The shell auto-opens the FIRST card, so this order decides which
        variable a person is shown."""
        h5py = pytest.importorskip("h5py")
        import numpy as np

        from quam_state_manager.core import ndview

        path = tmp_path / "ds_raw.h5"
        with h5py.File(path, "w") as f:
            d = f.create_dataset("detuning", data=np.linspace(-1e6, 1e6, 8))
            d.attrs["CLASS"] = np.bytes_("DIMENSION_SCALE")
            for name in ("state_10", "state_2", "state_1"):
                ds = f.create_dataset(name, data=np.zeros(8))
                ds.dims[0].attach_scale(d)
        probe = ndview.probe_file(path)
        assert probe["ok"], probe
        got = [v["name"] for v in probe["vars"]
               if v["name"].startswith("state_")]
        assert got == ["state_1", "state_2", "state_10"]


class TestCompareOrder:
    """``core/compare.py`` — the compare hub's structure card, the entity
    mapping line, and the Attention block."""

    def _snap(self, state, wiring=None):
        from quam_state_manager.core import compare as C
        from quam_state_manager.core.loader import QuamStore

        store = QuamStore.from_dicts(state, wiring or {})
        return C.build_snapshot(store, "h", wiring_missing=not wiring)

    def test_the_topology_card_lists_qubits_in_number_order(self):
        snap = self._snap({"qubits": {"q10": {"id": "q10"}, "q2": {"id": "q2"},
                                      "q1": {"id": "q1"}}})
        assert snap.qubits == ["q1", "q2", "q10"]

    def test_active_qubits_and_pairs_count(self):
        snap = self._snap({
            "qubits": {},
            "active_qubit_names": ["q10", "q2", "q1"],
            "active_qubit_pair_names": ["q1-q10", "q1-q2"],
        })
        assert snap.structure["active_qubits"] == ["q1", "q2", "q10"]
        assert snap.structure["active_pairs"] == ["q1-q2", "q1-q10"]

    def test_the_instrument_list_counts_controllers(self):
        snap = self._snap({"qubits": {}}, {
            "ports": {"mw_outputs": {"con10": {}, "con2": {}, "con1": {}}},
            "network": {"host": "10.1.1.6"},
        })
        assert snap.structure["instruments"] == [
            "mw_outputs/con1", "mw_outputs/con2", "mw_outputs/con10"]

    def test_unmatched_entities_are_listed_in_number_order(self):
        """``unmatched_a`` / ``unmatched_b`` are joined straight into the
        mapping line the user reads."""
        from quam_state_manager.core import compare as C

        a = self._snap({"qubits": {f"q{i}": {"id": f"q{i}"}
                                   for i in (1, 2, 10, 11)}})
        b = self._snap({"qubits": {"q1": {"id": "q1"}}})
        mr = C.auto_map_qubits(a, b)
        assert mr.unmatched_a == ["q2", "q10", "q11"]


class TestDiffRowOrder:
    """``core/json_diff.py`` — the two row builders behind the diff workbench
    and ``/diff/versions``. This one is the customer's own screenshot."""

    def test_added_leaves_of_one_array_count(self):
        """Every leaf of a newly-added array shares one rank, so the PATH is
        the tie-break — the exact place ``.1009`` outran ``.101``."""
        base = "qubits.q1.resonator.operations.readout.weights_imag."
        a = {"qubits": {"q1": {"resonator": {"operations": {
            "readout": {"length": 1000}}}}}}
        b = {"qubits": {"q1": {"resonator": {"operations": {"readout": {
            "length": 1000,
            "weights_imag": {str(i): 0.5
                             for i in (101, 1009, 1010, 1011)}}}}}}}
        rows = json_diff.diff_rows(a, b)["rows"]
        assert [r["path"] for r in rows] == [
            base + "101", base + "1009", base + "1010", base + "1011"]

    def test_the_n_way_version_rows_count(self):
        docs = [
            {"qubits": {"q1": {"w": {"101": 1, "1009": 1, "1011": 1}},
                        "q2": {"f": 1}, "q10": {"f": 1}}},
            {"qubits": {"q1": {"w": {"101": 2, "1009": 2, "1011": 2}},
                        "q2": {"f": 2}, "q10": {"f": 2}}},
        ]
        rows = json_diff.diff_rows_n(docs)["rows"]
        assert [r["path"] for r in rows] == [
            "qubits.q1.w.101", "qubits.q1.w.1009", "qubits.q1.w.1011",
            "qubits.q2.f", "qubits.q10.f"]


class TestCompareHubRowOrder:
    """``core/compare.py`` — the two per-group lists that reach the screen as
    a TRUNCATED join, so their order decides what a person is shown."""

    def _compare(self, tmp_path, state_a, state_b, wiring):
        from quam_state_manager.core import compare as C
        from quam_state_manager.core import compare_sources as cs

        pool, cache = cs.SourcePool(), C.SnapshotCache()
        for name, state in (("A", state_a), ("B", state_b)):
            folder = tmp_path / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "state.json").write_text(json.dumps(state),
                                               encoding="utf-8")
            (folder / "wiring.json").write_text(json.dumps(wiring),
                                                encoding="utf-8")
        srcs = [cs.resolve_source(f"ws:{tmp_path / n}", pool) for n in "AB"]
        return C.compare(srcs, pool, cache=cache)

    def test_the_attention_blocks_dangling_keys_count(self, tmp_path):
        """``unresolved_groups[i]['keys']`` is joined and TRUNCATED in the
        Attention block, so the order decides which paths are shown."""
        wiring = {"wiring": {}, "network": {"host": "10.1.1.6"}}

        def chip(where):
            return {"qubits": {f"q{i}": {
                "id": f"q{i}",
                "z": {"settle_time": f"#/wiring/gone_{where}"}}
                for i in (1, 2, 10, 11)}}

        res = self._compare(tmp_path, chip("a"), chip("b"), wiring)
        groups = res["attention"]["unresolved_groups"]
        assert groups, res["attention"]
        assert groups[0]["keys"] == [
            "qubits.q1.z.settle_time", "qubits.q2.z.settle_time",
            "qubits.q10.z.settle_time", "qubits.q11.z.settle_time"]

    def test_a_groups_collapsed_roots_count(self, tmp_path):
        """The collapsed one-sided roots render ABOVE the group's rows, which
        are already natural-ordered; they inherited ``union_sorted``'s
        lexicographic order, which the prefix bisect needs and must keep."""
        wiring = {"wiring": {}, "network": {"host": "10.1.1.6"}}
        base = {"qubits": {"q1": {"id": "q1", "f_01": 6.2e9, "xy": {
            "operations": {"p1": {"amplitude": 0.1, "length": 40}}}}}}
        added = json.loads(json.dumps(base))
        ops = added["qubits"]["q1"]["xy"]["operations"]
        for n in (2, 10):
            ops[f"p{n}"] = {"amplitude": 0.1, "length": 40}
        res = self._compare(tmp_path, base, added, wiring)
        roots = [c["root"] for g in res["groups"] for c in g["collapsed"]]
        assert roots == ["qubits.q1.xy.operations.p2",
                         "qubits.q1.xy.operations.p10"], roots
