"""v1.5.1 R3 MINOR 7: the user's own overlapping note saves are not "another
window".

Two saves of one run note in flight both carried the text stored before the
first as ``expected``; the server stored the first and refused the second,
and the user was told their own save came from another window -- the newer
text was not saved. Pinned on the REAL dataset detail fragment under jsdom
(tests/v151_r3_note_self_conflict_selfcheck.cjs); the server half of the
rule is checked here on the real route.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from tests.test_multifolder_datasets import _app_with_folders, _seed_run

ROOT = Path(__file__).resolve().parent.parent


def test_overlapping_saves_of_one_note_store_the_newest(tmp_path):
    f = tmp_path / "data"
    _seed_run(f, 1)
    app, c = _app_with_folders(tmp_path, [f])
    with app.app_context():
        uid = f"{routes._folder_key(f)}:1"
    # the real route's rule the fake server copies: a stale `expected` is refused
    assert c.post(f"/dataset/{uid}/note", json={"note": "abc", "expected": ""}).status_code == 200
    r = c.post(f"/dataset/{uid}/note", json={"note": "abcd", "expected": ""})
    assert r.status_code == 409 and r.get_json().get("note_conflict"), r.data[:300]
    assert c.post(f"/dataset/{uid}/note", json={"note": "", "expected": "abc"}).status_code == 200
    html = c.get(f"/dataset/{uid}", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert "ds-note-textarea" in html
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    fx = tmp_path / "fixture.json"
    fx.write_text(json.dumps({"detail_html": html, "stored": ""}), encoding="utf-8")
    r = subprocess.run([node, str(ROOT / "tests" / "v151_r3_note_self_conflict_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0 and "DONE" in r.stdout, r.stdout[-3000:] + r.stderr[-3000:]
