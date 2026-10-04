"""User request: a 2Q fidelity of ANY gate (an arbitrary flux waveform) and
ANY spelling must show on Chip Status > 2Q Fid. without a code change.

The server classifies every pair-fidelity row once (`query.fidelity_family`);
the panel builder only reads that key (tests/chip_status_adaptive_fid_selfcheck.cjs).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core.query import _extract_pair_gate_fidelities, fidelity_family, rb_level

_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("name, family", [
    ("StandardRB", "StandardRB"), ("standardrb", "StandardRB"), ("Standard_RB", "StandardRB"),
    ("SRB", "StandardRB"), ("srb", "StandardRB"),
    ("InterleavedRB", "InterleavedRB"), ("IRB", "InterleavedRB"), ("irb", "InterleavedRB"),
    ("Interleaved_RB", "InterleavedRB"), ("interleaved-rb", "InterleavedRB"),
    ("Bell_State", "Bell"), ("bell state", "Bell"), ("Bell", "Bell"),
    ("XEB", "XEB"), ("xeb", "XEB"), ("xeb_fidelity", "XEB"), ("CrossEntropy", "XEB"),
    ("MyGateScore", "other:MyGateScore"), ("RB", "other:RB"),
])
def test_every_spelling_has_one_family(name, family):
    assert fidelity_family(name) == family


@pytest.mark.parametrize("name", [
    "StandardRB_alpha", "irb_alpha", "error_per_gate", "XEB_err", "irb_std", "XEB_run",
    "StandardRB_load_id", "run_id", "", None,
])
def test_what_is_not_a_fidelity_has_no_family(name):
    assert fidelity_family(name) is None


def test_separator_spellings_share_the_rb_level():
    """The (0,1] physical bound follows the level (docs/138), so a separated
    spelling must not escape it."""
    assert rb_level("Interleaved_RB") == "gate" and rb_level("bell state") == "state"
    assert rb_level("SomeLabsOwnMetric") is None
    rows = _extract_pair_gate_fidelities({"cz_wave": {"fidelity": {"interleaved_rb": 1.53}}})
    assert rows[0]["level"] == "gate" and "value" not in rows[0] and rows[0]["raw_value"] == 1.53


def test_rows_carry_their_family_for_any_gate_name():
    rows = _extract_pair_gate_fidelities({
        "cz_my_waveform": {"fidelity": {"irb": 0.993, "xeb_fidelity": 0.985, "Bell": {"Fidelity": 0.96},
                                        "MyGateScore": 0.95, "irb_std": 0.0004}}})
    fam = {r["metric"]: r.get("family") for r in rows}
    assert fam == {"irb": "InterleavedRB", "xeb_fidelity": "XEB", "Bell": "Bell",
                   "MyGateScore": "other:MyGateScore"}
    irb = next(r for r in rows if r["metric"] == "irb")
    assert irb["gate"] == "cz_my_waveform" and irb.get("err") == 0.0004


def test_the_adaptive_panel_selfcheck_passes():
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    r = subprocess.run(["node", str(_ROOT / "tests" / "chip_status_adaptive_fid_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(_ROOT), timeout=120)
    if r.returncode == 2 and "jsdom not installed" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, (r.stdout + r.stderr)
