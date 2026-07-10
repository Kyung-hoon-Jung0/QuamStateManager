"""Tests for quam_state_manager.core.experiment_data."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from quam_state_manager.core.experiment_data import (
    ExperimentContext,
    load_experiment_context,
    _sanitize_fit,
)


def _write_experiment(folder: Path, *, node: dict | None = None, data: dict | None = None) -> Path:
    """Create an experiment folder with quam_state/, optional node.json, optional data.json."""
    qs = folder / "quam_state"
    qs.mkdir(parents=True, exist_ok=True)
    (qs / "state.json").write_text("{}", encoding="utf-8")
    (qs / "wiring.json").write_text("{}", encoding="utf-8")

    if node is not None:
        (folder / "node.json").write_text(json.dumps(node), encoding="utf-8")
    if data is not None:
        (folder / "data.json").write_text(json.dumps(data), encoding="utf-8")

    return qs


class TestLoadExperimentContext:
    def test_full_context(self, tmp_path):
        node = {
            "metadata": {
                "name": "03_resonator_spectroscopy_single",
                "status": "finished",
                "run_start": "2026-02-19T16:32:37",
                "run_end": "2026-02-19T16:34:19",
                "description": "  1D RESONATOR SPECTROSCOPY  ",
                "data_path": "2026-02-19/#11",
            },
            "data": {
                "parameters": {
                    "model": {
                        "num_shots": 100,
                        "frequency_span_in_mhz": 30.0,
                        "multiplexed": True,
                    },
                    "schema": {"type": "object"},
                },
                "outcomes": {"qC1": "successful", "qC2": "successful"},
            },
            "id": 11,
        }
        data = {
            "fit_results": {
                "qC1": {"frequency": 7.04e9, "fwhm": 1e7, "success": True},
                "qC2": {"frequency": 7.37e9, "fwhm": 6e6, "success": True},
            },
            "figures": {"phase": "./figures.phase.png"},
        }
        qs = _write_experiment(tmp_path / "exp1", node=node, data=data)
        ctx = load_experiment_context(qs)

        assert ctx.has_data is True
        assert ctx.experiment_name == "03_resonator_spectroscopy_single"
        assert ctx.metadata["name"] == "03_resonator_spectroscopy_single"
        assert ctx.metadata["status"] == "finished"
        assert ctx.metadata["description"] == "1D RESONATOR SPECTROSCOPY"
        assert ctx.parameters["num_shots"] == 100
        assert ctx.parameters["frequency_span_in_mhz"] == 30.0
        assert ctx.parameters["multiplexed"] is True
        assert "schema" not in ctx.parameters
        assert ctx.outcomes == {"qC1": "successful", "qC2": "successful"}
        assert ctx.fit_results["qC1"]["frequency"] == 7.04e9
        assert ctx.fit_results["qC2"]["success"] is True

    def test_no_experiment_files(self, tmp_path):
        qs = tmp_path / "standalone" / "quam_state"
        qs.mkdir(parents=True)
        (qs / "state.json").write_text("{}", encoding="utf-8")
        (qs / "wiring.json").write_text("{}", encoding="utf-8")

        ctx = load_experiment_context(qs)
        assert ctx.has_data is False
        assert ctx.metadata == {}
        assert ctx.parameters == {}
        assert ctx.fit_results == {}

    def test_only_node_json(self, tmp_path):
        node = {
            "metadata": {"name": "test_exp", "status": "running"},
            "data": {"parameters": {"model": {"x": 1}}, "outcomes": {}},
        }
        qs = _write_experiment(tmp_path / "exp2", node=node)
        ctx = load_experiment_context(qs)

        assert ctx.has_data is True
        assert ctx.experiment_name == "test_exp"
        assert ctx.parameters == {"x": 1}
        assert ctx.fit_results == {}

    def test_only_data_json(self, tmp_path):
        data = {"fit_results": {"qA1": {"value": 42.0}}}
        qs = _write_experiment(tmp_path / "exp3", data=data)
        ctx = load_experiment_context(qs)

        assert ctx.has_data is True
        assert ctx.fit_results == {"qA1": {"value": 42.0}}
        assert ctx.metadata == {}

    def test_nan_values_sanitized(self, tmp_path):
        data = {
            "fit_results": {
                "qA1": {"frequency": float("nan"), "success": False, "fwhm": float("inf")},
            }
        }
        qs = _write_experiment(tmp_path / "exp4", data=data)
        ctx = load_experiment_context(qs)

        assert ctx.fit_results["qA1"]["frequency"] is None
        assert ctx.fit_results["qA1"]["fwhm"] is None
        assert ctx.fit_results["qA1"]["success"] is False

    def test_file_path_references_become_none(self, tmp_path):
        """Relative-path fit values (HDF5 dataset refs) surface as ``None``
        so the display layer renders them as "–" instead of looking like
        the key never existed (red-team Phase 2 finding §3.6).
        """
        data = {
            "fit_results": {
                "qA1": {"frequency": 7e9, "m_pH": "./arrays.npz#fit_results.qA1.m_pH"},
            }
        }
        qs = _write_experiment(tmp_path / "exp5", data=data)
        ctx = load_experiment_context(qs)

        assert ctx.fit_results["qA1"]["m_pH"] is None
        assert ctx.fit_results["qA1"]["frequency"] == 7e9

    def test_non_quam_state_name(self, tmp_path):
        """When the path doesn't end in 'quam_state', parent is used as-is."""
        folder = tmp_path / "myexp"
        folder.mkdir()
        (folder / "state.json").write_text("{}", encoding="utf-8")
        (folder / "wiring.json").write_text("{}", encoding="utf-8")
        node = {"metadata": {"name": "test"}, "data": {}}
        (folder / "node.json").write_text(json.dumps(node), encoding="utf-8")

        ctx = load_experiment_context(folder)
        assert ctx.experiment_name == "test"


class TestSanitizeFit:
    def test_normal_values_pass_through(self):
        result = _sanitize_fit({"a": 1.0, "b": "hello", "c": True})
        assert result == {"a": 1.0, "b": "hello", "c": True}

    def test_nan_replaced_with_none(self):
        result = _sanitize_fit({"x": float("nan")})
        assert result["x"] is None

    def test_inf_replaced_with_none(self):
        result = _sanitize_fit({"x": float("inf"), "y": float("-inf")})
        assert result["x"] is None
        assert result["y"] is None

    def test_file_refs_become_none(self):
        """``./...`` strings (relative-path refs to external arrays) become
        ``None`` rather than being silently dropped (Phase 2 finding §3.6)."""
        result = _sanitize_fit({"a": 1, "b": "./some_file.npz#key"})
        assert result["b"] is None
        assert result["a"] == 1
