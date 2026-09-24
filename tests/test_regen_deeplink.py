"""QA F19: /regenerate?step=5 (the /instrument "Modify wiring..." deep link)
brings the Wiring step's rack into view on a short window.

Renders the real ``_regenerate.html`` fragment through Flask (so the Jinja step
literal is the one the route produces), then runs its inline bootstrap under
node + jsdom (tests/regen_deeplink_selfcheck.cjs) with a stubbed reconstruct
answer and the geometry measured in real Chrome at 1366x768.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "regen_deeplink_selfcheck.cjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


@pytest.fixture
def client(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    return app.test_client()


def _run(client, tmp_path, url, case):
    resp = client.get(url, headers={"HX-Request": "true"})
    assert resp.status_code == 200
    page = tmp_path / "regen.html"
    page.write_text(resp.get_data(as_text=True), encoding="utf-8")
    proc = subprocess.run(["node", str(_SELFCHECK), str(page), case],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert line, proc.stdout + proc.stderr
    return json.loads(line[-1][len("RESULT "):])


def test_step5_deep_link_scrolls_the_allocate_row_to_the_top(client, tmp_path):
    r = _run(client, tmp_path, "/regenerate?step=5", "scroll")
    assert r["hydrated"] == {"step": 5, "mode": "regenerate"}
    assert len(r["scrolled"]) == 1, r
    assert "gen-allocate-row" in r["scrolled"][0]["cls"]
    assert r["scrolled"][0]["block"] == "start"


def test_a_warning_note_is_never_scrolled_out_of_sight(client, tmp_path):
    r = _run(client, tmp_path, "/regenerate?step=5", "notes")
    assert r["hydrated"]["step"] == 5
    assert r["scrolled"] == [], r
    assert "a carried pin could bite" in r["status"]


def test_a_tall_window_is_left_alone(client, tmp_path):
    r = _run(client, tmp_path, "/regenerate?step=5", "tall")
    assert r["scrolled"] == [], r


def test_plain_regenerate_does_not_scroll(client, tmp_path):
    r = _run(client, tmp_path, "/regenerate", "scroll")
    assert r["hydrated"] == {"step": None, "mode": "regenerate"}
    assert r["scrolled"] == [], r
