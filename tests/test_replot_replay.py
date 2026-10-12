"""Saved-input replay pins using synthetic source trees and run data."""
import sys

from quam_state_manager.generator import run_interactive_replot as runner


def test_module_resolution_reads_node_imports(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    nodes = tmp_path / "calibrations" / "group"
    nodes.mkdir(parents=True)
    for name, module in (("01_alias", "actual"), ("02_alias_dsp", "actual_dsp"),
                         ("03_power", "amplitude")):
        (nodes / (name + ".py")).write_text(
            f"from calibration_utils.{module} import plot_data as draw\n"
            "raise RuntimeError('acquisition must not execute')\n", encoding="utf-8")
        resolved, tree, imports = runner._resolve_node("1Q_" + name, str(tmp_path))
        assert resolved == "calibration_utils." + module
        assert tree is not None
        assert imports["draw"] == (resolved, "plot_data")
    assert str(nodes) in sys.path
    assert str(tmp_path / "calibrations") in sys.path


def test_module_resolution_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    assert runner._resolve_node("1Q_04_unlisted", str(tmp_path)) == (
        "calibration_utils.unlisted", None, {})
