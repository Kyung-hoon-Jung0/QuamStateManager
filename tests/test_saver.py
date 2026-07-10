"""Tests for quam_state_manager.core.saver.Saver.

Covers atomic save, backup creation, change-log clearing, pointer preservation,
CSV export, Markdown export, and round-trip integrity.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier
from quam_state_manager.core.saver import Saver, _format_value

# ---------------------------------------------------------------------------
# Paths to real quam_state folders
# ---------------------------------------------------------------------------

_EXAMPLECHIP_ROOT = Path(r"<data-root>/qualibration_graphs/superconducting")
SMALL_FOLDER = _EXAMPLECHIP_ROOT / "quam_state"
LARGE_FOLDER = _EXAMPLECHIP_ROOT / "quam_states_arv" / "quam_state_examplechip_variantb"

has_small = SMALL_FOLDER.exists() and (SMALL_FOLDER / "state.json").exists()
has_large = LARGE_FOLDER.exists() and (LARGE_FOLDER / "state.json").exists()

skip_no_small = pytest.mark.skipif(not has_small, reason="Small quam_state not found")
skip_no_large = pytest.mark.skipif(not has_large, reason="Large quam_state not found")


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------

def _make_state():
    return {
        "qubits": {
            "qA1": {
                "id": "qA1",
                "f_01": 6.25e9,
                "anharmonicity": -220e6,
                "T1": 8834,
                "T2ramsey": 1.5e-6,
                "T2echo": None,
                "grid_location": "0,2",
                "xy": {
                    "RF_frequency": 6.25e9,
                    "opx_output": "#/wiring/qubits/qA1/xy/opx_output",
                    "operations": {
                        "x180_DragCosine": {"amplitude": 0.115, "length": 40, "alpha": -1.75},
                    },
                },
                "resonator": {
                    "f_01": 7.64e9,
                    "operations": {
                        "readout": {"amplitude": 0.042, "length": 1000, "threshold": -0.00014},
                    },
                },
                "z": {"joint_offset": 0.081, "flux_point": "joint"},
                "gate_fidelity": {"averaged": 0.991},
            },
            "qA2": {
                "id": "qA2",
                "f_01": 5.8e9,
                "anharmonicity": -210e6,
                "T1": 7500,
                "T2ramsey": 1.2e-6,
                "T2echo": None,
                "grid_location": "1,2",
                "xy": {
                    "RF_frequency": 5.8e9,
                    "operations": {
                        "x180_DragCosine": {"amplitude": 0.13, "length": 40, "alpha": -1.5},
                    },
                },
                "resonator": {
                    "f_01": 7.1e9,
                    "operations": {
                        "readout": {"amplitude": 0.05, "length": 1000, "threshold": -0.0002},
                    },
                },
                "z": {"joint_offset": 0.0, "flux_point": "joint"},
                "gate_fidelity": {"averaged": 0.985},
            },
        },
        "qubit_pairs": {},
        "active_qubit_names": ["qA1", "qA2"],
    }


def _make_wiring():
    return {
        "wiring": {
            "qubits": {
                "qA1": {
                    "xy": {"opx_output": "MW-FEM/1/2"},
                    "rr": {"opx_output": "MW-FEM/1/1"},
                    "z": {"opx_output": "LF-FEM/5/1"},
                },
                "qA2": {
                    "xy": {"opx_output": "MW-FEM/1/3"},
                    "rr": {"opx_output": "MW-FEM/1/1"},
                    "z": {"opx_output": "LF-FEM/5/2"},
                },
            },
        },
        "network": {"host": "10.1.1.18", "cluster_name": "test"},
    }


@pytest.fixture
def synth_folder(tmp_path: Path) -> Path:
    (tmp_path / "state.json").write_text(json.dumps(_make_state(), indent=4), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_make_wiring(), indent=4), encoding="utf-8")
    return tmp_path


@pytest.fixture
def store(synth_folder) -> QuamStore:
    return QuamStore(synth_folder)


@pytest.fixture
def saver(store) -> Saver:
    return Saver(store)


# ---------------------------------------------------------------------------
# Atomic save
# ---------------------------------------------------------------------------


class TestSave:
    def test_save_creates_files(self, saver, tmp_path):
        out = tmp_path / "output"
        saver.save(out)
        assert (out / "state.json").exists()
        assert (out / "wiring.json").exists()

    def test_save_roundtrip_preserves_data(self, saver, tmp_path):
        out = tmp_path / "output"
        saver.save(out)

        reloaded = QuamStore(out)
        assert reloaded.merged["qubits"]["qA1"]["f_01"] == 6.25e9
        assert reloaded.merged["network"]["host"] == "10.1.1.18"

    def test_save_preserves_pointer_strings(self, saver, tmp_path):
        out = tmp_path / "output"
        saver.save(out)

        with open(out / "state.json", encoding="utf-8") as f:
            saved_state = json.load(f)
        assert saved_state["qubits"]["qA1"]["xy"]["opx_output"] == "#/wiring/qubits/qA1/xy/opx_output"

    def test_save_clears_change_log(self, store, saver, tmp_path):
        mod = Modifier(store)
        mod.set_value("qubits.qA1.f_01", 6.3e9)
        assert len(store.change_log) == 1

        saver.save(tmp_path / "output")
        assert len(store.change_log) == 0

    def test_save_in_place(self, synth_folder, store):
        mod = Modifier(store)
        mod.set_value("qubits.qA1.f_01", 6.3e9)
        saver = Saver(store)
        saver.save()

        reloaded = QuamStore(synth_folder)
        assert reloaded.merged["qubits"]["qA1"]["f_01"] == 6.3e9

    def test_save_after_modification_roundtrips(self, store, tmp_path):
        mod = Modifier(store)
        mod.set_value("qubits.qA1.T1", 9999)
        mod.set_value("network.host", "10.2.2.20")

        out = tmp_path / "output"
        saver = Saver(store)
        saver.save(out)

        reloaded = QuamStore(out)
        assert reloaded.merged["qubits"]["qA1"]["T1"] == 9999
        assert reloaded.merged["network"]["host"] == "10.2.2.20"

    def test_save_writes_valid_json(self, saver, tmp_path):
        out = tmp_path / "output"
        saver.save(out)

        with open(out / "state.json", encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert "qubits" in data

        with open(out / "wiring.json", encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert "wiring" in data


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


class TestBackup:
    def test_backup_created(self, synth_folder, store):
        saver = Saver(store)
        saver.save()

        bak_files = list(synth_folder.glob("*.bak.*"))
        assert len(bak_files) == 2
        names = [f.name for f in bak_files]
        assert any("state.json.bak." in n for n in names)
        assert any("wiring.json.bak." in n for n in names)

    def test_backup_content_matches_original(self, synth_folder, store):
        original_state = json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))

        mod = Modifier(store)
        mod.set_value("qubits.qA1.f_01", 6.3e9)

        saver = Saver(store)
        saver.save()

        bak_files = list(synth_folder.glob("state.json.bak.*"))
        assert len(bak_files) == 1
        bak_content = json.loads(bak_files[0].read_text(encoding="utf-8"))
        assert bak_content["qubits"]["qA1"]["f_01"] == original_state["qubits"]["qA1"]["f_01"]

    def test_no_backup_for_new_folder(self, saver, tmp_path):
        out = tmp_path / "brand_new"
        saver.save(out)
        bak_files = list(out.glob("*.bak.*"))
        assert len(bak_files) == 0

    def test_multiple_saves_create_multiple_backups(self, synth_folder, store):
        import time

        saver = Saver(store)
        saver.save()
        time.sleep(1.1)
        saver.save()

        state_baks = list(synth_folder.glob("state.json.bak.*"))
        assert len(state_baks) >= 2

    def test_backup_rotation_prunes_old_files(self, synth_folder, store):
        """Older backups beyond the retention limit must be deleted."""
        saver = Saver(store, backup_retention=3)
        # Seed 8 ancient backups for state.json (timestamps in 2020).
        for i in range(8):
            stamp = f"2020010{i}_120000"
            (synth_folder / f"state.json.bak.{stamp}").write_text("old", encoding="utf-8")
            (synth_folder / f"wiring.json.bak.{stamp}").write_text("old", encoding="utf-8")
        # Saving creates one new bak (with today's stamp) and rotation must drop us to 3 total.
        saver.save()
        state_baks = sorted(synth_folder.glob("state.json.bak.*"))
        wiring_baks = sorted(synth_folder.glob("wiring.json.bak.*"))
        assert len(state_baks) == 3
        assert len(wiring_baks) == 3
        # The oldest seeds (20200100..20200105) must have been pruned;
        # the two newest seeds (20200106, 20200107) survive alongside today's bak.
        kept_state = {p.name for p in state_baks}
        for old_stamp in ("20200100_120000", "20200101_120000", "20200105_120000"):
            assert not any(old_stamp in n for n in kept_state), f"{old_stamp} should have been pruned"
        assert any("20200107_120000" in n for n in kept_state)
        assert any("20200106_120000" in n for n in kept_state)

    def test_backup_retention_default_is_20(self, synth_folder, store):
        saver = Saver(store)
        assert saver.backup_retention == 20


# ---------------------------------------------------------------------------
# No .tmp files left behind
# ---------------------------------------------------------------------------


class TestNoTmpFiles:
    def test_no_tmp_after_save(self, saver, tmp_path):
        out = tmp_path / "output"
        saver.save(out)
        tmp_files = list(out.glob("*.tmp"))
        assert len(tmp_files) == 0


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


class TestExportCSV:
    def test_csv_created(self, saver, tmp_path):
        csv_path = tmp_path / "summary.csv"
        saver.export_csv(csv_path)
        assert csv_path.exists()

    def test_csv_has_header(self, saver, tmp_path):
        csv_path = tmp_path / "summary.csv"
        saver.export_csv(csv_path)
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            # Default mode labels dimensioned columns with their display unit.
            assert "id" in reader.fieldnames
            assert "f_01_GHz" in reader.fieldnames
            assert "T1_us" in reader.fieldnames

    def test_csv_has_correct_rows(self, saver, tmp_path):
        csv_path = tmp_path / "summary.csv"
        saver.export_csv(csv_path)
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2
        ids = [r["id"] for r in rows]
        assert "qA1" in ids
        assert "qA2" in ids

    def test_csv_custom_properties(self, saver, tmp_path):
        csv_path = tmp_path / "custom.csv"
        saver.export_csv(csv_path, properties=["f_01", "T1"])
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert list(reader.fieldnames) == ["id", "f_01_GHz", "T1_us"]

    def test_csv_values_correct(self, saver, tmp_path):
        csv_path = tmp_path / "vals.csv"
        saver.export_csv(csv_path, properties=["f_01", "T1"])
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        qa1 = [r for r in rows if r["id"] == "qA1"][0]
        # Values are converted to the labeled display unit: Hz->GHz, s->µs.
        assert float(qa1["f_01_GHz"]) == 6.25e9 / 1e9
        assert float(qa1["T1_us"]) == 8834 * 1e6

    def test_csv_raw_mode_preserves_legacy(self, saver, tmp_path):
        """with_units=False -> bare headers + raw SI values (legacy pipelines)."""
        csv_path = tmp_path / "raw.csv"
        saver.export_csv(csv_path, properties=["f_01", "T1"], with_units=False)
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert list(reader.fieldnames) == ["id", "f_01", "T1"]
            rows = list(reader)
        qa1 = [r for r in rows if r["id"] == "qA1"][0]
        assert float(qa1["f_01"]) == 6.25e9
        assert int(qa1["T1"]) == 8834

    def test_csv_creates_parent_dirs(self, saver, tmp_path):
        csv_path = tmp_path / "deep" / "nested" / "summary.csv"
        saver.export_csv(csv_path)
        assert csv_path.exists()


# ---------------------------------------------------------------------------
# Markdown export
# ---------------------------------------------------------------------------


class TestExportMarkdown:
    def test_md_created(self, saver, tmp_path):
        md_path = tmp_path / "summary.md"
        saver.export_markdown(md_path)
        assert md_path.exists()

    def test_md_has_table_structure(self, saver, tmp_path):
        md_path = tmp_path / "summary.md"
        saver.export_markdown(md_path)
        text = md_path.read_text(encoding="utf-8")
        lines = text.strip().split("\n")
        assert len(lines) >= 4  # header + separator + 2 data rows
        assert lines[0].startswith("| ")
        assert lines[1].startswith("| ")
        assert "---" in lines[1]

    def test_md_contains_ids(self, saver, tmp_path):
        md_path = tmp_path / "summary.md"
        saver.export_markdown(md_path)
        text = md_path.read_text(encoding="utf-8")
        assert "qA1" in text
        assert "qA2" in text

    def test_md_custom_properties(self, saver, tmp_path):
        md_path = tmp_path / "custom.md"
        saver.export_markdown(md_path, properties=["f_01", "T1"])
        text = md_path.read_text(encoding="utf-8")
        header = text.strip().split("\n")[0]
        assert "id" in header
        assert "f_01" in header
        assert "T1" in header

    def test_md_creates_parent_dirs(self, saver, tmp_path):
        md_path = tmp_path / "deep" / "nested" / "summary.md"
        saver.export_markdown(md_path)
        assert md_path.exists()


# ---------------------------------------------------------------------------
# _format_value
# ---------------------------------------------------------------------------


class TestFormatValue:
    def test_none(self):
        assert _format_value(None) == "-"

    def test_large_float(self):
        result = _format_value(6.25e9)
        assert "e" in result.lower()

    def test_small_float(self):
        result = _format_value(1.5e-6)
        assert "e" in result.lower()

    def test_normal_float(self):
        result = _format_value(0.042)
        assert "0.042" in result

    def test_int(self):
        assert _format_value(8834) == "8834"

    def test_string(self):
        assert _format_value("0,2") == "0,2"

    def test_list(self):
        assert _format_value([1, 2, 3]) == "[...]"

    def test_dict(self):
        assert _format_value({"a": 1}) == "{...}"


# ---------------------------------------------------------------------------
# Real data tests
# ---------------------------------------------------------------------------


@skip_no_large
class TestLargeRealData:
    def test_save_roundtrip(self, tmp_path):
        store = QuamStore(LARGE_FOLDER)
        saver = Saver(store)
        out = tmp_path / "roundtrip"
        saver.save(out)

        reloaded = QuamStore(out)
        assert reloaded.merged["qubits"]["qA1"]["f_01"] == store.merged["qubits"]["qA1"]["f_01"]
        assert len(reloaded.qubit_names) == len(store.qubit_names)
        assert len(reloaded.qubit_pair_names) == len(store.qubit_pair_names)

    def test_save_preserves_all_pointers(self, tmp_path):
        store = QuamStore(LARGE_FOLDER)
        saver = Saver(store)
        out = tmp_path / "ptr_check"
        saver.save(out)

        with open(out / "state.json", encoding="utf-8") as f:
            saved = json.load(f)
        assert saved["qubits"]["qA1"]["xy"]["opx_output"] == "#/wiring/qubits/qA1/xy/opx_output"

    def test_modify_save_roundtrip(self, tmp_path):
        store = QuamStore(LARGE_FOLDER)
        mod = Modifier(store)
        mod.set_value("qubits.qA1.f_01", 6.5e9)

        out = tmp_path / "modified"
        saver = Saver(store)
        saver.save(out)

        reloaded = QuamStore(out)
        assert reloaded.merged["qubits"]["qA1"]["f_01"] == 6.5e9

    def test_csv_export(self, tmp_path):
        store = QuamStore(LARGE_FOLDER)
        saver = Saver(store)
        csv_path = tmp_path / "large.csv"
        saver.export_csv(csv_path)

        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) >= 16

    def test_markdown_export(self, tmp_path):
        store = QuamStore(LARGE_FOLDER)
        saver = Saver(store)
        md_path = tmp_path / "large.md"
        saver.export_markdown(md_path)

        text = md_path.read_text(encoding="utf-8")
        lines = text.strip().split("\n")
        assert len(lines) >= 18  # header + sep + 16+ rows

    def test_saved_json_size_reasonable(self, tmp_path):
        store = QuamStore(LARGE_FOLDER)
        saver = Saver(store)
        out = tmp_path / "size_check"
        saver.save(out)

        state_size = (out / "state.json").stat().st_size
        wiring_size = (out / "wiring.json").stat().st_size
        assert state_size > 100_000
        assert wiring_size > 100


# ---------------------------------------------------------------------------
# Edge cases — atomic write safety
# ---------------------------------------------------------------------------


class TestSaverEdgeCases:

    def test_backup_created_before_save(self, tmp_path: Path):
        folder = tmp_path / "quam_state"
        folder.mkdir()
        (folder / "state.json").write_text('{"qubits": {}}', encoding="utf-8")
        (folder / "wiring.json").write_text('{}', encoding="utf-8")
        store = QuamStore(folder)
        saver = Saver(store)
        saver.save(folder)
        bak_files = list(folder.glob("*.bak.*"))
        assert len(bak_files) >= 1

    def test_tmp_file_not_left_on_success(self, tmp_path: Path):
        folder = tmp_path / "quam_state"
        folder.mkdir()
        (folder / "state.json").write_text('{"qubits": {}}', encoding="utf-8")
        (folder / "wiring.json").write_text('{}', encoding="utf-8")
        store = QuamStore(folder)
        saver = Saver(store)
        saver.save(folder)
        tmp_files = list(folder.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_save_to_nonexistent_dir_creates_it(self, tmp_path: Path):
        folder = tmp_path / "source"
        folder.mkdir()
        (folder / "state.json").write_text('{"qubits": {}}', encoding="utf-8")
        (folder / "wiring.json").write_text('{}', encoding="utf-8")
        store = QuamStore(folder)
        saver = Saver(store)
        out = tmp_path / "new_dir"
        saver.save(out)
        assert (out / "state.json").exists()
        assert (out / "wiring.json").exists()
