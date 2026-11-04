"""Drives tests/diagram_add_selfcheck.cjs (the add door under jsdom) and pins
the markup the door needs on the pages that have it, and only there."""
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "diagram_add_selfcheck.cjs"
_TPL = _ROOT / "quam_state_manager" / "web" / "templates"


def test_selfcheck():
    r = subprocess.run(["node", str(_SELFCHECK)], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT), timeout=120)
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)


def test_only_the_qubits_and_pairs_pages_open_the_door():
    for name in ("_qubits.html", "_pairs.html"):
        assert "set cmap_add" in (_TPL / name).read_text(encoding="utf-8"), name
    for name in ("_flux.html", "_couplers.html", "_resonators.html", "_pulses.html", "_qdac.html"):
        assert "cmap_add" not in (_TPL / name).read_text(encoding="utf-8"), name


def test_the_map_markup_is_unchanged_without_the_door():
    t = (_TPL / "_component_map.html").read_text(encoding="utf-8")
    assert '{% if cmap_add %} data-cm-add="1"{% endif %}' in t
    assert '{% if cmap_add %}<div id="cm-add-panel"' in t
