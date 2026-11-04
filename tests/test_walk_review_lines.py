"""Walk G6: Review's "Control lines" names the coupler lines a coupler-only CZ
chip is built with (it listed only "Resonator + XY drive")."""
from pathlib import Path

import pytest

JS = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "static"
      / "generate.js").read_text(encoding="utf-8")


def test_coupler_only_cz_lines_are_listed_on_review():
    i = JS.index('if (mw) lineTypes.push("Resonator + XY drive");')
    block = JS[i:i + 1500]
    assert 'pairGate === "cz_coupler" && lf) lineTypes.push("Coupler-only CZ (coupler flux)")' in block
