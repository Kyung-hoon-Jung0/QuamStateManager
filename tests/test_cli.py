"""Tests for quam_state_manager.cli (Typer CLI).

Uses typer.testing.CliRunner to invoke commands programmatically
and verify output/exit codes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quam_state_manager.cli import app, _parse_value

runner = CliRunner()

# ---------------------------------------------------------------------------
# Paths to real quam_state folders
# ---------------------------------------------------------------------------

_EXAMPLECHIP_ROOT = Path(r"<data-root>/qualibration_graphs/superconducting")
LARGE_FOLDER = _EXAMPLECHIP_ROOT / "quam_states_arv" / "quam_state_examplechip_variantb"
DATA_ROOT = _EXAMPLECHIP_ROOT / "data" / "project_name"

has_large = LARGE_FOLDER.exists() and (LARGE_FOLDER / "state.json").exists()
has_data = DATA_ROOT.exists()

skip_no_large = pytest.mark.skipif(not has_large, reason="Large quam_state not found")
skip_no_data = pytest.mark.skipif(not has_data, reason="Experiment data folder not found")


# ---------------------------------------------------------------------------
# Synthetic fixture
# ---------------------------------------------------------------------------

def _make_state():
    return {
        "qubits": {
            "qA1": {
                "id": "qA1",
                "f_01": 6.25e9,
                "T1": 8834,
                "T2ramsey": 1.5e-6,
                "T2echo": None,
                "anharmonicity": -220e6,
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
                    "RF_frequency": 7.64e9,
                    "operations": {
                        "readout": {"amplitude": 0.042, "length": 1000, "threshold": -0.00014},
                    },
                },
                "z": {"joint_offset": 0.081, "flux_point": "joint"},
                "gate_fidelity": {"averaged": 0.991, "x180": 0.986, "x90": 0.986},
            },
        },
        "qubit_pairs": {
            "qA1-A2": {
                "id": "qA1-A2",
                "qubit_control": "#/qubits/qA1",
                "qubit_target": "#/qubits/qA1",
                "moving_qubit": "target",
                "macros": {},
                "coupler": {"decouple_offset": 0.48, "interaction_offset": 0.0},
                "detuning": 0.032,
            },
        },
        "active_qubit_names": ["qA1"],
    }


def _make_wiring():
    return {
        "wiring": {
            "qubits": {
                "qA1": {
                    "xy": {"opx_output": "MW-FEM/1/2"},
                    "rr": {"opx_output": "MW-FEM/1/1", "opx_input": "MW-FEM/1/1"},
                    "z": {"opx_output": "LF-FEM/5/1"},
                },
            },
        },
        "network": {"host": "10.1.1.18"},
    }


@pytest.fixture
def synth_folder(tmp_path: Path) -> Path:
    (tmp_path / "state.json").write_text(json.dumps(_make_state(), indent=2), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# _parse_value
# ---------------------------------------------------------------------------


class TestParseValue:
    def test_none(self):
        assert _parse_value("null") is None
        assert _parse_value("none") is None
        assert _parse_value("None") is None

    def test_bool(self):
        assert _parse_value("true") is True
        assert _parse_value("false") is False

    def test_int(self):
        assert _parse_value("42") == 42
        assert isinstance(_parse_value("42"), int)

    def test_float(self):
        assert _parse_value("3.14") == 3.14
        assert _parse_value("6.25e9") == 6.25e9

    def test_string(self):
        assert _parse_value("hello") == "hello"
        assert _parse_value("MW-FEM/1/2") == "MW-FEM/1/2"

    def test_grouped_commas_stripped(self):
        # thousands-grouped numbers (Bulk Edit display) parse to the bare number
        assert _parse_value("5,075,187,484") == 5075187484
        assert isinstance(_parse_value("5,075,187,484"), int)
        assert _parse_value("1,234.5") == 1234.5
        assert _parse_value("-256,900,000.0") == -256900000.0

    def test_mixed_or_loose_grouping_strips(self):
        # users mix comma + plain ("7,662,072100") — any digits-with-commas string
        # is treated as a grouped number and the commas are stripped.
        assert _parse_value("7,662,072100") == 7662072100
        assert _parse_value("1,23,456") == 123456
        assert _parse_value("1,5") == 15            # comma = grouping (this app uses '.' decimal)

    def test_genuine_strings_untouched(self):
        # non-numeric strings (must start with a digit/sign to be a number) are kept
        assert _parse_value("a,b") == "a,b"
        assert _parse_value("MW,FEM") == "MW,FEM"
        assert _parse_value("#/wiring/qubits/qA1/xy") == "#/wiring/qubits/qA1/xy"

    def test_non_finite_rejected(self):
        import pytest
        for bad in ("inf", "-inf", "nan", "Infinity", "1e999"):
            with pytest.raises(ValueError):
                _parse_value(bad)

    def test_roundtrip_with_group_digits(self):
        from quam_state_manager.core.units import group_digits
        for v in (5050000000, 5075187484.52453, 7460000000.0, 0.215,
                  -0.00014, -256900000.0, 0, 800, -2.0, 1.5):
            assert _parse_value(group_digits(v)) == v

    def test_quoted_string_is_unwrapped(self):
        # the double-quote bug: typing `"02"` to force a STRING must yield the
        # 2-char string `02`, never `"02"` (which JSON-stored as "\"02\"").
        assert _parse_value('"02"') == "02"
        assert isinstance(_parse_value('"02"'), str)
        assert _parse_value('"hello"') == "hello"
        assert _parse_value('"5,075"') == "5,075"      # quoted → literal, commas kept
        assert _parse_value('"true"') == "true"        # quoted → the string, not bool

    def test_json_list_parsed(self):
        assert _parse_value("[1, 2, 3]") == [1, 2, 3]
        assert _parse_value("[[0.98, 0.02], [0.02, 0.98]]") == [[0.98, 0.02], [0.02, 0.98]]
        assert _parse_value('["qA1", "qA2"]') == ["qA1", "qA2"]
        assert _parse_value("[]") == []

    def test_json_object_parsed(self):
        assert _parse_value('{"a": 1, "b": [2, 3]}') == {"a": 1, "b": [2, 3]}
        assert _parse_value("{}") == {}

    def test_bare_values_unchanged_by_json_branch(self):
        # the JSON branch is gated on a "/[/{ lead char, so bare scalars keep their
        # existing handling — `02` is still int 2, a pointer/word stays a string.
        assert _parse_value("02") == 2
        assert _parse_value("42") == 42
        assert _parse_value("hello") == "hello"
        assert _parse_value("#/wiring/qubits/qA1/xy") == "#/wiring/qubits/qA1/xy"

    def test_malformed_json_falls_back_to_string(self):
        # an unterminated/garbage literal is kept as the raw string (the modifier's
        # type-coercion then accepts or rejects it against the field's old type).
        assert _parse_value("[1, 2") == "[1, 2"
        assert _parse_value("[draft]") == "[draft]"
        assert _parse_value("{oops") == "{oops"


# ---------------------------------------------------------------------------
# show command
# ---------------------------------------------------------------------------


class TestShowCommand:
    def test_show_qubit(self, synth_folder):
        result = runner.invoke(app, ["show", "qA1", "-f", str(synth_folder)])
        assert result.exit_code == 0
        assert "qA1" in result.output
        assert "f_01" in result.output

    def test_show_qubit_section(self, synth_folder):
        result = runner.invoke(app, ["show", "qA1", "-f", str(synth_folder), "--section", "coherence"])
        assert result.exit_code == 0
        assert "T1" in result.output
        assert "T2ramsey" in result.output

    def test_show_pair(self, synth_folder):
        result = runner.invoke(app, ["show", "qA1-A2", "-f", str(synth_folder)])
        assert result.exit_code == 0
        assert "qA1-A2" in result.output
        assert "detuning" in result.output

    def test_show_missing_qubit(self, synth_folder):
        result = runner.invoke(app, ["show", "qZZZ", "-f", str(synth_folder)])
        assert result.exit_code == 1

    def test_show_invalid_section(self, synth_folder):
        result = runner.invoke(app, ["show", "qA1", "-f", str(synth_folder), "--section", "invalid"])
        assert result.exit_code == 1

    def test_show_invalid_folder(self, tmp_path):
        result = runner.invoke(app, ["show", "qA1", "-f", str(tmp_path / "nonexistent")])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# table command
# ---------------------------------------------------------------------------


class TestTableCommand:
    def test_table(self, synth_folder):
        result = runner.invoke(app, ["table", "f_01", "T1", "-f", str(synth_folder)])
        assert result.exit_code == 0
        assert "qA1" in result.output
        assert "f_01" in result.output


# ---------------------------------------------------------------------------
# wiring command
# ---------------------------------------------------------------------------


class TestWiringCommand:
    def test_wiring(self, synth_folder):
        result = runner.invoke(app, ["wiring", "-f", str(synth_folder)])
        assert result.exit_code == 0
        assert "qA1" in result.output
        assert "MW-FEM" in result.output


# ---------------------------------------------------------------------------
# search command
# ---------------------------------------------------------------------------


class TestSearchCommand:
    def test_search_value(self, synth_folder):
        result = runner.invoke(app, ["search", "7640", "-f", str(synth_folder)])
        assert result.exit_code == 0

    def test_search_key(self, synth_folder):
        result = runner.invoke(app, ["search", "qA1", "-f", str(synth_folder)])
        assert result.exit_code == 0

    def test_search_no_results(self, synth_folder):
        result = runner.invoke(app, ["search", "zzzzzzzzzzz", "-f", str(synth_folder)])
        assert result.exit_code == 0
        assert "No results" in result.output


# ---------------------------------------------------------------------------
# set command
# ---------------------------------------------------------------------------


class TestSetCommand:
    def test_set_value(self, synth_folder):
        result = runner.invoke(app, ["set", "qubits.qA1.f_01", "6.3e9", "-f", str(synth_folder)])
        assert result.exit_code == 0
        assert "Value Set" in result.output

    def test_set_invalid_path(self, synth_folder):
        result = runner.invoke(app, ["set", "qubits.nonexistent.f_01", "1.0", "-f", str(synth_folder)])
        assert result.exit_code == 1

    def test_set_with_save(self, synth_folder):
        result = runner.invoke(app, ["set", "qubits.qA1.T1", "9000", "-f", str(synth_folder), "--save"])
        assert result.exit_code == 0
        assert "Saved" in result.output

        reloaded = json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))
        assert reloaded["qubits"]["qA1"]["T1"] == 9000


# ---------------------------------------------------------------------------
# diff command
# ---------------------------------------------------------------------------


class TestDiffCommand:
    def test_diff_identical(self, synth_folder):
        result = runner.invoke(app, ["diff", str(synth_folder), str(synth_folder)])
        assert result.exit_code == 0
        assert "No differences" in result.output

    def test_diff_modified(self, synth_folder, tmp_path):
        state = _make_state()
        state["qubits"]["qA1"]["f_01"] = 6.3e9
        other = tmp_path / "other"
        other.mkdir()
        (other / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        (other / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")

        result = runner.invoke(app, ["diff", str(synth_folder), str(other)])
        assert result.exit_code == 0
        assert "modified" in result.output.lower()


# ---------------------------------------------------------------------------
# export command
# ---------------------------------------------------------------------------


class TestExportCommand:
    def test_export_csv(self, synth_folder, tmp_path):
        csv_path = tmp_path / "out.csv"
        result = runner.invoke(app, ["export", str(csv_path), "-f", str(synth_folder)])
        assert result.exit_code == 0
        assert csv_path.exists()
        assert "Exported" in result.output

    def test_export_markdown(self, synth_folder, tmp_path):
        md_path = tmp_path / "out.md"
        result = runner.invoke(app, ["export", str(md_path), "-f", str(synth_folder)])
        assert result.exit_code == 0
        assert md_path.exists()

    def test_export_bad_format(self, synth_folder, tmp_path):
        result = runner.invoke(app, ["export", str(tmp_path / "out.xyz"), "-f", str(synth_folder)])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# scan command
# ---------------------------------------------------------------------------


class TestScanCommand:
    def test_scan_empty(self, tmp_path):
        result = runner.invoke(app, ["scan", str(tmp_path)])
        assert result.exit_code == 0
        assert "No quam_state" in result.output

    def test_scan_synthetic(self, synth_folder):
        result = runner.invoke(app, ["scan", str(synth_folder)])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Real data tests
# ---------------------------------------------------------------------------


@skip_no_large
class TestRealShowCommand:
    def test_show_qA1(self):
        result = runner.invoke(app, ["show", "qA1", "-f", str(LARGE_FOLDER)])
        assert result.exit_code == 0
        assert "6.255526e+09" in result.output or "6255526" in result.output

    def test_show_pair(self):
        result = runner.invoke(app, ["show", "qA1-A2", "-f", str(LARGE_FOLDER)])
        assert result.exit_code == 0
        assert "qA1-A2" in result.output

    def test_table(self):
        result = runner.invoke(app, ["table", "f_01", "T2ramsey", "-f", str(LARGE_FOLDER)])
        assert result.exit_code == 0
        assert "qA1" in result.output

    def test_search(self):
        result = runner.invoke(app, ["search", "7639", "-f", str(LARGE_FOLDER)])
        assert result.exit_code == 0

    def test_wiring(self):
        result = runner.invoke(app, ["wiring", "-f", str(LARGE_FOLDER)])
        assert result.exit_code == 0


@skip_no_data
class TestRealScanCommand:
    def test_scan_experiments(self):
        result = runner.invoke(app, ["scan", str(DATA_ROOT), "-n", "5"])
        assert result.exit_code == 0
        assert "experiment" in result.output.lower() or "Found" in result.output

    def test_scan_with_date_filter(self):
        result = runner.invoke(app, ["scan", str(DATA_ROOT), "--date", "2026-02-19"])
        assert result.exit_code == 0


class TestVersionResolution:
    """The CLI must resolve its version without crashing even when the package
    imports as a namespace (broken/duplicate editable install → __init__.py never
    runs, so `from quam_state_manager import __version__` would ImportError)."""

    def test_version_flag_works(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "quam-manager" in result.output
        # a real version string, not the unknown fallback
        assert "0.0.0+unknown" not in result.output

    def test_resolve_version_prefers_dist_metadata(self):
        from quam_state_manager import cli
        v = cli._resolve_version()
        assert isinstance(v, str) and v and v != "0.0.0+unknown"

    def test_resolve_version_survives_namespace_package(self, monkeypatch):
        # Simulate the namespace-package failure mode: both the dist metadata AND
        # the package attribute are unavailable. The CLI must degrade to the
        # literal fallback, never raise.
        import importlib.metadata as im
        from quam_state_manager import cli

        def _boom(_name):
            raise im.PackageNotFoundError("quam-state-manager")

        monkeypatch.setattr(im, "version", _boom)
        # also make the attribute import fail (namespace package has no __version__)
        import builtins
        real_import = builtins.__import__

        def _fake_import(name, *a, **k):
            if name == "quam_state_manager" and a and "__version__" in (a[2] or ()):
                raise ImportError("cannot import name '__version__' (unknown location)")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        assert cli._resolve_version() == "0.0.0+unknown"   # graceful, no crash
