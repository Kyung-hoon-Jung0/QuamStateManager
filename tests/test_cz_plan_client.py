"""Execute the shipped cz-plan.js under jsdom (card, notes, picker, Last set column, late answers)."""
import os
import subprocess
from pathlib import Path

import pytest


def test_cz_plan_client():
    root = Path(__file__).resolve().parents[1]
    probe = subprocess.run(["node", "-e", "require('jsdom')"], cwd=root, env=os.environ.copy(),
                           capture_output=True, text=True)
    if probe.returncode:
        pytest.skip("jsdom not installed")
    result = subprocess.run(["node", str(root / "tests/cz_plan_selfcheck.cjs")], cwd=root, env=os.environ.copy(),
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    # v1.4.1: 31 -> 35, include the production render queue and panel invalidation pins.
    # v1.4.1 (A14): 35 -> 57, the notes box / status chip / caveat lines (D1), the flux figure's
    # loss panel (D2) and the card's scan-loss and defects-touched lines (D3).
    assert "57 assertions passed" in result.stdout


def test_cz_chip_map_client():
    """The chip frequency map (cz-map.js): columns, segment styles, refusals, picker, order, click,
    the chip drawing joined to the columns, colours, labels, highlight, the measured loss."""
    root = Path(__file__).resolve().parents[1]
    probe = subprocess.run(["node", "-e", "require('jsdom')"], cwd=root, env=os.environ.copy(),
                           capture_output=True, text=True)
    if probe.returncode:
        pytest.skip("jsdom not installed")
    result = subprocess.run(["node", str(root / "tests/cz_map_selfcheck.cjs")], cwd=root, env=os.environ.copy(),
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    # v1.4.1: 17 -> 34, include numeric widths, production hover/purge and render queue pins.
    # v1.4.1 (A14): 34 -> 76, the chip drawing (shear order, leaders, logical / none), the edge
    # colouring, bar and partner labels, selection and highlight, the trace bound and the loss.
    assert "76 assertions passed" in result.stdout


def test_the_page_reads_title_status_figures_card_then_the_collapsed_notes(tmp_path):
    """A14 F1/F2 on the template: the operation's title row (with the staleness chip), the
    visible status line, each figure with its caveat line, the card, and only then the notes
    box -- collapsed. The lede is one line with the physics behind "?"; the map's controls
    sit in its title row, columns by chip position and the paper's 0-25 % loss scale first."""
    from tests.test_cz_plan_page import _client
    html = _client(tmp_path).get("/cz-plan", headers={"HX-Request": "true"}).get_data(as_text=True)
    ids = ("czp-title", "czp-stale-chip", "czp-status", "czp-flux-caveat", "czp-flux", "czp-time-caveat", "czp-time",
           "czp-card", "czp-notes-box", "czp-notes")
    at = [html.index(f'id="{i}"') for i in ids]
    assert at == sorted(at), dict(zip(ids, at))
    for box in ("czp-notes-box", "czp-map-detail"):
        tag = html[html.index(f'<details id="{box}"'):].split(">", 1)[0]
        assert " open" not in tag, tag
    lede = html[html.index('class="muted czp-lede"'):]
    lede = lede[:lede.index("</details>")]
    assert '<details class="czp-help">' in lede and "|11&rang;&ndash;|20&rang;" in lede.split("<details", 1)[1]
    assert "|11&rang;" not in lede.split("<details", 1)[0], "the physics sits behind the ? only"
    head = html[html.index("czp-map-head"):html.index('id="czp-map"')]
    assert all(f'id="{i}"' in head for i in ("czp-map-op", "czp-map-order", "czp-map-scale"))
    assert '<option value="chip" selected>By chip position</option>' in head
    assert '<option value="0.25" selected>0-25 % (paper)</option>' in head
