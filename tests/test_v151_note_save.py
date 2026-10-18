"""v1.5.1 U P2-22: a run note has a visible Save and a saved state.

Pinned on the REAL dataset detail fragment under jsdom
(tests/v151_note_save_selfcheck.cjs): the open note shows Save and a state
line; typing says "Not saved yet"; Save sends once and says "Saved HH:MM";
Ctrl+Enter saves, Enter adds a line; leaving the box still saves.
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


def test_a_run_note_has_a_save_and_says_it_saved(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    f = tmp_path / "data"
    _seed_run(f, 1)
    app, c = _app_with_folders(tmp_path, [f])
    with app.app_context():
        uid = f"{routes._folder_key(f)}:1"
    html = c.get(f"/dataset/{uid}", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert "ds-note-save" in html
    fx = tmp_path / "fixture.json"
    fx.write_text(json.dumps({"detail_html": html}), encoding="utf-8")
    r = subprocess.run([node, str(ROOT / "tests" / "v151_note_save_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0 and "DONE" in r.stdout, r.stdout[-3000:] + r.stderr[-3000:]
