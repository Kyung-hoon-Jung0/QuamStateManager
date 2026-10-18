"""v1.5.1 U P2-27: the Settings sliders are labelled ("Size", "Letter gap",
"Row gap" instead of "Aa" and two arrow glyphs), and the Config Manual opened
by F1 on a value closes on Escape. Pinned on a REAL full page in
``tests/v151_settings_manual_selfcheck.cjs``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_sliders_are_named_and_escape_closes_the_manual(tmp_path):
    page = create_app(testing=True, instance_path=str(tmp_path / "_i")).test_client().get("/").get_data(as_text=True)
    assert 'id="manual-popover"' in page and "settings-slider" in page
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"page": page}), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_settings_manual_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120)
    if p.returncode == 2 and "jsdom not installed" in (p.stderr or ""):
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"
