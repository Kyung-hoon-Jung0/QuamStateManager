"""The Diagnostics findings list follows the state (docs/141 4f).

The topbar pill and the banner re-fetch on ``diagnostics-changed``; the
page's own findings list was a one-shot render, so after a sync / undo /
apply fixed the values the red rows stayed until F5 (user report,
2026-08-28). The list now lives in a slot that lifts itself out of a fresh
``/diagnostics`` render (``hx-select``) on the same trigger.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"


def _write_chip(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps({
        "qubits": {"qA1": {"id": "qA1", "f_01": 5.0e9, "z": {"joint_offset": 0.08},
                           "xy": {"ops": {"x180": {"amp": 0.2}}}}},
        "qubit_pairs": {}, "active_qubit_names": ["qA1"],
    }), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}), encoding="utf-8")


@pytest.fixture
def client(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    r = c.post("/load", data={"folder": str(live)})
    assert r.status_code in (200, 302)
    return c


def test_the_findings_list_is_a_self_refreshing_slot(client):
    r = client.get("/diagnostics", headers={"HX-Request": "true"})
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    i = html.index('id="diag-findings"')
    head = html[i:i + 400]
    assert 'hx-get="/diagnostics"' in head and 'hx-select="#diag-findings"' in head \
        and 'hx-swap="outerHTML"' in head
    assert "diagnostics-changed from:body" in head and "stateRestored from:body" in head
    # the list itself sits INSIDE the slot (one render path, no second route)
    assert html.index('id="diag-filter-bar"') > i
    assert html.count('id="diag-findings"') == 1


def test_the_full_page_carries_the_same_slot(client):
    html = client.get("/diagnostics").get_data(as_text=True)
    assert html.count('id="diag-findings"') == 1 and 'hx-select="#diag-findings"' in html


def test_the_filter_is_reapplied_after_the_inner_swap():
    app = (_STATIC / "app.js").read_text(encoding="utf-8")
    # docs/141 4l-review: the inner swap also restores the user's folded domains
    seg = app.split("if (evt.detail.target.id === 'diag-findings') {")[1].split("return;")[0]
    assert "_diagOpenSnap" in seg and "_applyDiagFilter();" in seg
    assert "details.diag-domain[data-domain]" in app.split("var _diagOpenSnap = null;")[1][:700]


def test_the_slot_does_not_leak_its_select_and_swap_to_its_links(client):
    """QA diagnostics-r2-01: hx-select / hx-swap are INHERITED htmx attributes.

    Without ``hx-disinherit`` the Config Viewer links inside the slot selected
    ``#diag-findings`` out of ``/config`` (nothing) and outerHTML-swapped
    ``#table-pane`` itself away: blank pane, dead sidebar until F5.
    """
    html = client.get("/diagnostics", headers={"HX-Request": "true"}).get_data(as_text=True)
    i = html.index('id="diag-findings"')
    tag = html[i:html.index(">", i)]
    assert 'hx-disinherit="' in tag
    val = tag.split('hx-disinherit="')[1].split('"')[0].split()
    assert "hx-select" in val and "hx-swap" in val


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node not available")
def test_the_config_viewer_link_keeps_the_pane_under_real_htmx(client, tmp_path):
    """Behaviour, not markup: the real bundled htmx clicks the real fragment's
    link against a local server; a control run without the disinherit must
    reproduce the loss (diag_findings_disinherit_fragcheck.cjs)."""
    import subprocess
    html = client.get("/diagnostics", headers={"HX-Request": "true"}).get_data(as_text=True)
    frag = tmp_path / "diag_fragment.html"
    frag.write_text(html, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(_ROOT / "tests" / "diag_findings_disinherit_fragcheck.cjs"), str(frag)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stdout + proc.stderr):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
