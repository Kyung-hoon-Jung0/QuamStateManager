"""On-site report: a CZ gate whose only 2Q figure is XEB was invisible on
Chip Status > 2Q Fid., and its flat fidelity block was misread.

The device's state stores XEB as sibling keys, not a nested dict::

    "fidelity": {"XEB": 0.9889, "XEB_err": 0.0012, "XEB_run": 1782,
                 "XEB_runs": [1782, 1783], "XEB_updated_at": "..."}

Read key by key, ``XEB_err`` (an uncertainty) and ``XEB_run`` (a run number)
became fidelity rows of their own, the XEB row carried another gate's RB run
as its provenance, and Trends offered the run number as a 2Q measurement.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core.query import _extract_pair_gate_fidelities, fidelity_field_kind

_ROOT = Path(__file__).resolve().parent.parent

FLAT = {"XEB": 0.9889, "XEB_err": 0.0012, "XEB_run": 1782,
        "XEB_runs": [1782, 1783], "XEB_updated_at": "2026-10-04T10:45:00+09:00"}


def _rows(fid: dict, gate: str = "cz_SNZ") -> list[dict]:
    return _extract_pair_gate_fidelities({gate: {"fidelity": fid}})


def test_a_flat_xeb_block_is_one_row_with_its_attributes():
    rows = _rows(FLAT)
    assert [r["metric"] for r in rows] == ["XEB"]
    row = rows[0]
    assert row["value"] == 0.9889 and row["err"] == 0.0012
    assert row["runs"] == [1782, 1783] and row["updated_at"] == "2026-10-04T10:45:00+09:00"


def test_the_xeb_row_names_its_own_run_not_the_gates_rb_run():
    """With an RB run id on the same gate, the XEB row used to inherit it."""
    rows = _rows({**FLAT, "StandardRB_load_id": 331,
                  "StandardRB": {"average_gate_fidelity": 0.99}})
    xeb = next(r for r in rows if r["metric"] == "XEB")
    srb = next(r for r in rows if r["metric"] == "StandardRB")
    assert xeb["load_id"] == 1782
    assert srb["load_id"] == 331


def test_a_run_number_is_provenance_everywhere():
    for key in ("XEB_run", "XEB_runs", "run", "runs", "StandardRB_run"):
        assert fidelity_field_kind(key) == "load_id", key


def test_trends_never_offers_a_run_number_as_a_2q_measurement():
    from quam_state_manager.web import routes
    assert not routes._is_2q_measurement("macros.cz_SNZ.fidelity.XEB_run")
    assert routes._is_2q_measurement("macros.cz_SNZ.fidelity.XEB")


def test_unrelated_suffixes_and_dict_blocks_keep_their_old_reading():
    """Only the named attribute suffixes group, and only beside a SCALAR metric:
    a scalar IRB's decay base is still its own decay row, a dict block's
    *_load_id is still skipped, and a lone *_err with no metric beside it is
    left to the per-key rules."""
    rows = _rows({"InterleavedRB": 0.991, "InterleavedRB_alpha": 0.98})
    assert sorted((r["metric"], r["level"]) for r in rows) == [
        ("InterleavedRB", "gate"), ("InterleavedRB_alpha", "decay")]
    rows = _rows({"StandardRB": {"average_gate_fidelity": 0.97, "alpha": 0.96},
                  "StandardRB_load_id": 529})
    assert [r["metric"] for r in rows] == ["StandardRB"] and rows[0]["load_id"] == 529
    rows = _rows({"Lone_err": 0.01})
    assert [r["metric"] for r in rows] == ["Lone_err"]


def test_the_xeb_panel_selfcheck_passes():
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    r = subprocess.run(["node", str(_ROOT / "tests" / "chip_status_xeb_panel_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(_ROOT), timeout=120)
    if r.returncode == 2 and "jsdom not installed" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, (r.stdout + r.stderr)
