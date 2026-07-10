"""End-to-end integration tests exercising the full pipeline.

Each test chains multiple modules together, verifying that data flows correctly
from load through search, modify, save, diff, and compare.  Tests run against
both a synthetic fixture and the real 3-qubit / 17-qubit folders.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.search_index import SearchIndex
from quam_state_manager.core.query import QueryEngine
from quam_state_manager.core.modifier import Modifier
from quam_state_manager.core.saver import Saver
from quam_state_manager.core.differ import Differ
from quam_state_manager.core.scanner import Workspace

_EXAMPLECHIP_ROOT = Path(r"<data-root>/qualibration_graphs/superconducting")
SMALL_FOLDER = _EXAMPLECHIP_ROOT / "quam_state"
LARGE_FOLDER = _EXAMPLECHIP_ROOT / "quam_states_arv" / "quam_state_examplechip_variantb"
DATA_ROOT = _EXAMPLECHIP_ROOT / "data" / "project_name"

has_small = SMALL_FOLDER.exists() and (SMALL_FOLDER / "state.json").exists()
has_large = LARGE_FOLDER.exists() and (LARGE_FOLDER / "state.json").exists()
has_data = DATA_ROOT.exists()

skip_no_small = pytest.mark.skipif(not has_small, reason="Small quam_state folder not found")
skip_no_large = pytest.mark.skipif(not has_large, reason="Large quam_state folder not found")
skip_no_data = pytest.mark.skipif(not has_data, reason="Experiment data folder not found")


@pytest.fixture
def synthetic_folder(tmp_path):
    """Minimal quam_state folder (no wiring overlap with state keys)."""
    state = {
        "__class__": "quam.components.quantum_components.QuAM",
        "qubits": {
            "q1": {
                "__class__": "quam.components.quantum_components.Transmon",
                "id": "q1",
                "f_01": 5_000_000_000.0,
                "anharmonicity": -200_000_000,
                "T1": 5000,
                "T2ramsey": 3000,
                "xy": {
                    "opx_output": "#/wiring/qubits/q1/port_I",
                    "intermediate_frequency": 50_000_000,
                    "operations": {
                        "x180_DragCosine": {"amplitude": 0.25, "length": 40},
                        "x90_DragCosine": {"amplitude": 0.125, "length": "#../x180_DragCosine/length"},
                    },
                },
                "resonator": {
                    "opx_output": "#/wiring/qubits/q1/port_I",
                    "intermediate_frequency": 100_000_000,
                    "readout_amplitude": 0.1,
                },
            },
            "q2": {
                "__class__": "quam.components.quantum_components.Transmon",
                "id": "q2",
                "f_01": 6_000_000_000.0,
                "anharmonicity": -210_000_000,
                "T1": 7000,
                "T2ramsey": 4500,
                "xy": {
                    "opx_output": "#/wiring/qubits/q2/port_I",
                    "intermediate_frequency": 60_000_000,
                    "operations": {
                        "x180_DragCosine": {"amplitude": 0.30, "length": 36},
                        "x90_DragCosine": {"amplitude": 0.15, "length": "#../x180_DragCosine/length"},
                    },
                },
                "resonator": {
                    "opx_output": "#/wiring/qubits/q2/port_I",
                    "intermediate_frequency": 110_000_000,
                    "readout_amplitude": 0.12,
                },
            },
        },
        "qubit_pairs": {
            "q1-q2": {
                "__class__": "quam.components.quantum_components.TransmonPair",
                "qubit_control": "#/qubits/q1",
                "qubit_target": "#/qubits/q2",
            },
        },
        "network": {"host": "192.168.1.100", "cluster_name": "test_cluster"},
        "active_qubit_names": ["q1", "q2"],
    }
    wiring = {
        "wiring": {
            "qubits": {
                "q1": {"port_I": [["con1", 1, 1]], "port_Q": [["con1", 1, 2]]},
                "q2": {"port_I": [["con1", 2, 1]], "port_Q": [["con1", 2, 2]]},
            },
        },
        "network": {"host": "192.168.1.100", "cluster_name": "test_cluster"},
    }
    folder = tmp_path / "quam_state"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(state, indent=2))
    (folder / "wiring.json").write_text(json.dumps(wiring, indent=2))
    return folder


class TestSyntheticPipeline:
    """Full pipeline on synthetic data: load -> search -> modify -> save -> diff."""

    def test_load_search_modify_save_diff(self, synthetic_folder, tmp_path):
        store = QuamStore(synthetic_folder)
        assert "q1" in store.qubit_names
        assert "q2" in store.qubit_names

        idx = SearchIndex.build(store.merged)
        results = idx.search("5000000000")
        assert len(results) >= 1

        eng = QueryEngine(store)
        q1 = eng.get_qubit("q1")
        assert q1["f_01"] == 5_000_000_000.0

        mod = Modifier(store)
        mod.set_value("qubits.q1.f_01", 5_100_000_000.0)
        assert store.get_value("qubits.q1.f_01") == 5_100_000_000.0
        assert len(store.change_log) == 1

        out = tmp_path / "saved"
        out.mkdir()
        saver = Saver(store)
        saver.save(out)
        assert (out / "state.json").exists()

        differ = Differ()
        entries = differ.diff(synthetic_folder, out)
        modified = [e for e in entries if e.change_type == "modified"]
        assert len(modified) >= 1
        freq_change = next(e for e in modified if "f_01" in e.dot_path)
        assert freq_change.old_value == 5_000_000_000.0
        assert freq_change.new_value == 5_100_000_000.0

    def test_undo_restores_original(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        mod = Modifier(store)
        original = store.get_value("qubits.q1.f_01")
        mod.set_value("qubits.q1.f_01", 9.9e9)
        assert store.get_value("qubits.q1.f_01") == 9.9e9
        mod.undo()
        assert store.get_value("qubits.q1.f_01") == original

    def test_pointer_resolution_in_query(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        raw_len = store.get_value("qubits.q1.xy.operations.x90_DragCosine.length")
        assert isinstance(raw_len, str) and raw_len.startswith("#")
        resolved = store.resolve_value("qubits.q1.xy.operations.x90_DragCosine.length")
        assert resolved == 40

    def test_search_after_modify_reflects_change(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        idx = SearchIndex.build(store.merged)
        store.search_index = idx
        mod = Modifier(store)
        mod.set_value("qubits.q1.T1", 12345)
        results = idx.search("12345")
        assert any("T1" in r.dot_path and "12345" in str(r.raw_value) for r in results)

    def test_csv_export_roundtrip(self, synthetic_folder, tmp_path):
        store = QuamStore(synthetic_folder)
        saver = Saver(store)
        csv_path = tmp_path / "export.csv"
        saver.export_csv(csv_path, ["f_01", "T1"])
        content = csv_path.read_text()
        assert "q1" in content
        assert "q2" in content

    def test_wiring_map_and_topology(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        eng = QueryEngine(store)
        wmap = eng.get_wiring_map()
        assert len(wmap) == 2
        assert any(r["qubit"] == "q1" for r in wmap)

        topo = eng.get_topology()
        assert len(topo["nodes"]) == 2
        assert len(topo["edges"]) >= 1

    def test_summary_table_all_qubits(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        eng = QueryEngine(store)
        table = eng.summary_table(["f_01", "T1", "T2ramsey"])
        assert len(table) == 2
        assert table[0]["id"] in ("q1", "q2")
        assert table[1]["id"] in ("q1", "q2")
        for row in table:
            assert "f_01" in row
            assert "T1" in row
            assert "T2ramsey" in row


@skip_no_large
class TestRealPipeline:
    """Full pipeline on the 17-qubit real dataset."""

    def test_load_search_query(self):
        store = QuamStore(LARGE_FOLDER)
        assert len(store.qubit_names) >= 16
        assert len(store.qubit_pair_names) >= 20

        idx = SearchIndex.build(store.merged)
        assert idx.stats()["entries"] > 1000

        results = idx.search("qA1 f_01")
        assert len(results) >= 1

        eng = QueryEngine(store)
        qA1 = eng.get_qubit("qA1")
        assert "f_01" in qA1
        assert isinstance(qA1["f_01"], (int, float))

    def test_modify_save_diff_roundtrip(self, tmp_path):
        store = QuamStore(LARGE_FOLDER)
        mod = Modifier(store)
        old_freq = store.get_value("qubits.qA1.f_01")
        new_freq = old_freq + 1_000_000
        mod.set_value("qubits.qA1.f_01", new_freq)

        out = tmp_path / "modified"
        out.mkdir()
        saver = Saver(store)
        saver.save(out)

        differ = Differ()
        entries = differ.diff(LARGE_FOLDER, out)
        freq_changes = [e for e in entries if "qA1" in e.dot_path and "f_01" in e.dot_path]
        assert len(freq_changes) == 1
        assert freq_changes[0].old_value == old_freq
        assert freq_changes[0].new_value == new_freq

    def test_saved_state_reloads_correctly(self, tmp_path):
        store = QuamStore(LARGE_FOLDER)
        mod = Modifier(store)
        mod.set_value("qubits.qA1.T1", 99999)

        out = tmp_path / "reloaded"
        out.mkdir()
        saver = Saver(store)
        saver.save(out)

        store2 = QuamStore(out)
        assert store2.get_value("qubits.qA1.T1") == 99999
        assert len(store2.qubit_names) == len(store.qubit_names)

    def test_pointer_resolution_survives_save(self, tmp_path):
        store = QuamStore(LARGE_FOLDER)
        resolved_before = store.resolve_value("qubits.qA1.xy.opx_output")

        out = tmp_path / "ptr_test"
        out.mkdir()
        saver = Saver(store)
        saver.save(out)

        store2 = QuamStore(out)
        resolved_after = store2.resolve_value("qubits.qA1.xy.opx_output")
        assert resolved_before == resolved_after

    def test_csv_and_markdown_export(self, tmp_path):
        store = QuamStore(LARGE_FOLDER)
        saver = Saver(store)

        csv_path = tmp_path / "export.csv"
        saver.export_csv(csv_path, ["f_01", "T1", "T2ramsey"])
        csv_text = csv_path.read_text()
        assert "qA1" in csv_text

        md_path = tmp_path / "export.md"
        saver.export_markdown(md_path, ["f_01", "T1", "T2ramsey"])
        md_text = md_path.read_text()
        assert "qA1" in md_text
        assert "|" in md_text

    def test_search_performance_under_5ms(self):
        import time
        store = QuamStore(LARGE_FOLDER)
        idx = SearchIndex.build(store.merged)

        queries = ["qA1", "f_01", "T1", "readout", "qA1 T2", "port flux"]
        for q in queries:
            start = time.perf_counter()
            idx.search(q)
            elapsed_ms = (time.perf_counter() - start) * 1000
            assert elapsed_ms < 5, f"Search '{q}' took {elapsed_ms:.1f}ms (>5ms threshold)"

    def test_full_topology_has_all_edges(self):
        store = QuamStore(LARGE_FOLDER)
        eng = QueryEngine(store)
        topo = eng.get_topology()
        assert len(topo["nodes"]) >= 16
        assert len(topo["edges"]) >= 20


@skip_no_data
class TestWorkspacePipeline:
    """Integration tests for workspace scanning + multi-state compare."""

    def test_scan_and_compare_experiments(self):
        ws = Workspace()
        entries = ws.add_root(DATA_ROOT)
        assert len(entries) >= 2

        quam_entries = [e for e in entries if e.quam_state_path is not None]
        if len(quam_entries) < 2:
            pytest.skip("Need at least 2 experiments with quam_state for compare")

        stores = [ws.load_store(e.quam_state_path) for e in quam_entries[:3]]
        labels = [e.run_id or str(e.quam_state_path) for e in quam_entries[:3]]

        differ = Differ()
        trend = differ.multi_compare(
            stores=stores,
            labels=labels,
            properties=["f_01"],
        )
        assert len(trend) >= 1
        for row in trend:
            assert "qubit" in row
            assert "property" in row
            assert len(row["values"]) == len(stores)

    def test_workspace_load_store_is_cached(self):
        ws = Workspace()
        entries = ws.add_root(DATA_ROOT)
        quam_entries = [e for e in entries if e.quam_state_path is not None]
        if not quam_entries:
            pytest.skip("No experiments with quam_state found")

        path = quam_entries[0].quam_state_path
        store1 = ws.load_store(path)
        store2 = ws.load_store(path)
        assert store1 is store2


@skip_no_large
class TestWebIntegration:
    """Integration tests that verify the web layer works end-to-end."""

    @pytest.fixture(autouse=True)
    def setup_app(self):
        from quam_state_manager.web.app import create_app
        app = create_app(testing=True)
        self.client = app.test_client()
        self.client.post("/load", data={"folder": str(LARGE_FOLDER)})

    def test_load_then_search_then_detail(self):
        resp = self.client.get("/search?q=qA1+f_01")
        assert resp.status_code == 200
        assert b"qA1" in resp.data

        resp = self.client.get("/qubit/qA1")
        assert resp.status_code == 200
        assert b"f_01" in resp.data

    def test_edit_then_undo_via_web(self):
        resp = self.client.post(
            "/qubit/qA1/edit",
            data={"dot_path": "qubits.qA1.f_01", "value": "7000000000"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

        resp = self.client.post("/undo", follow_redirects=True)
        assert resp.status_code == 200

    def test_export_csv_via_web(self):
        resp = self.client.get("/export?fmt=csv")
        assert resp.status_code == 200
        assert b"qA1" in resp.data

    def test_diff_two_identical_folders(self):
        resp = self.client.post(
            "/diff",
            data={"path_a": str(LARGE_FOLDER), "path_b": str(LARGE_FOLDER)},
        )
        assert resp.status_code == 200

    def test_table_renders_all_qubits(self):
        resp = self.client.get("/table")
        assert resp.status_code == 200
        assert b"qA1" in resp.data

    def test_wiring_renders_topology(self):
        resp = self.client.get("/wiring")
        assert resp.status_code == 200
