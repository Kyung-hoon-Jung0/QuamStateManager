"""Real-archive replay tier (docs/56 §6-R) — auto-skips off this workstation.

Two pinned anchor runs from the real qubit-spec-vs-power chain validate the
whole offline pipeline: the node-faithful refit + replot subprocess works over
a real run; the clean anchor agrees; and the KNOWN-BAD anchor (#575 — success
claimed on a window with no discernible peak; +1.5 MHz off the later stable
value) is documented as REFIT-BLIND (fresh analysis agrees with the stored
noise pick) — the case that mandates the vision round. If gate tightening
ever makes the bad anchor deterministically caught, this test should be
UPDATED to pin that stronger behavior, never weakened.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from quam_state_manager.core.autofit import replay

_BAD = Path("/mnt/d/work_laptop/dataset/KRISS/2026-06-14/"
             "#575_1Q_08b_qubit_spectroscopy_vs_power_104031")
_GOOD = Path("/mnt/d/work_laptop/dataset/KRISS/2026-06-14/"
             "#578_1Q_08b_qubit_spectroscopy_vs_power_104626")

pytestmark = pytest.mark.skipif(
    not (_BAD.is_dir() and _GOOD.is_dir()
         and Path(replay.DEFAULT_PYTHON).exists()),
    reason="real archive + QM replay env not available")


def test_clean_anchor_gates_pass_and_refit_agrees(tmp_path):
    row = replay.evaluate_run(_GOOD, tmp_path / "good", fix="none")
    assert row["family"] == "qubit_spectroscopy_vs_power"
    t = row["targets"]["qA1"]
    assert t["gate_verdict"] == "pass", t
    assert t["refit_code"] == "agrees", t
    assert row["refit_figures"], "refit figure was not rendered"
    assert row["stored_figures"], "stored figure missing"


def test_bad_anchor_is_refit_blind_and_flagged_for_vision(tmp_path):
    row = replay.evaluate_run(_BAD, tmp_path / "bad", fix="none")
    t = row["targets"]["qA1"]
    # the stored claim sits ~1.5 MHz off the later-accepted value, yet the
    # node-faithful replay AGREES with it (self-consistent noise fit) — the
    # documented refit blind spot that the vision auditor must cover
    assert t["refit_code"] in ("agrees", "drift"), t
    # both figure sides for the vision pair exist
    assert row["stored_figures"] and row["refit_figures"]
