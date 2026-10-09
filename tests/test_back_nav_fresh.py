"""w7 final QA (P2): a full-page Back never shows an old value, nor a number in
the wrong field.

Repro (real Chrome, lab-F-5q): Enter-edit ``qubits.q2.chi`` on /bulk, leave by a
FULL navigation (typed URL, non-htmx link), press Back. Before the fix:

* the page came from the HTTP cache (navigation type ``back_forward``,
  transferSize 0) and q2.chi showed the PRE-edit value, for good;
* Chrome's form-state restore dropped the typed number into a DIFFERENT cell
  (``qubits.q3.f_12`` showed -346,937.0) -- the grid's inputs are unnamed, so
  restore matches them by ORDER, against a DOM the grid's JS re-orders.

Measured separately, each defence is load-bearing on its own:

* ``Cache-Control: no-store`` on full pages -> Back refetches (fresh value),
  but Chrome STILL restored the typed number into q3.f_12 on the refetched
  page (so no-store alone is not enough);
* ``autocomplete="off"`` on every grid/inspector edit input -> nothing is
  saved or restored into them;
* a ``pageshow`` freshness probe -> a page that still comes from a cache
  (bfcache ``persisted``, or a cache that ignored no-store) asks the tray's
  seq beacon and refetches (with no-store removed, the probe alone turned the
  stale cell fresh).

The header half lives in ``test_web.py::TestPhase5CacheControl``; the pageshow
half in ``pane_state_selfcheck.cjs`` section 12 (driven by test_pane_state.py).
"""
from __future__ import annotations

import json
import re

import pytest

from quam_state_manager.web.app import create_app
from tests import _ram_chip


@pytest.fixture
def client(tmp_path):
    s, w = _ram_chip.build(5, 2)
    chip = tmp_path / "chip"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps(s), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps(w), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    return c


_INPUT = re.compile(r"<input\b[^>]*>", re.S)


def _inputs_with_class(html: str, cls: str) -> list[str]:
    out = []
    for tag in _INPUT.findall(html):
        m = re.search(r'class="([^"]*)"', tag)
        if m and cls in m.group(1).split():
            out.append(tag)
    return out


def test_every_grid_cell_is_out_of_the_browsers_form_restore(client):
    """Qubit AND pair grid, editable AND read-only cells: a read-only input
    is still a target of an order-matched restore (its value is settable)."""
    html = client.get("/bulk").get_data(as_text=True)
    cells = _inputs_with_class(html, "bulk-cell")
    # the fixture really renders both kinds, on both grids
    assert len(cells) > 50
    assert any("readonly" in t for t in cells)
    assert any("readonly" not in t for t in cells)
    assert any('data-dot-path="qubit_pairs.' in t for t in cells)
    missing = [t[:160] for t in cells if 'autocomplete="off"' not in t]
    assert not missing, f"{len(missing)} grid cells without autocomplete=off: {missing[:3]}"


@pytest.mark.parametrize("url", ["/qubit/q1", "/pair/q1-2"])
def test_the_inspector_edit_inputs_are_out_of_it_too(client, url):
    html = client.get(url).get_data(as_text=True)
    edits = _inputs_with_class(html, "edit-input")
    assert edits, f"fixture: {url} renders edit inputs"
    missing = [t[:160] for t in edits if 'autocomplete="off"' not in t]
    assert not missing, missing[:3]
