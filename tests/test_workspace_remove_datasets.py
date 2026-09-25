"""QA datasets-r2-26 -- removing a data folder with the sidebar x.

Two gaps: the Datasets table never heard about it (the response swapped only
``#sidebar-tree``), and the per-run routes resolved the REMOVED folder's uid to
the remaining folder's run of the same number (the single-folder drift
fallback -- run ids restart at #1 in every folder, so an explicit remove is
exactly the "mid-session multi->single transition AND a colliding id" that
fallback assumed was rare). The client half (the table re-reads itself) is
pinned by ``tests/ds_roots_changed_selfcheck.cjs``.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from tests.test_multifolder_datasets import _app_with_folders, _seed_run

HX = {"HX-Request": "true"}
_ROOT = Path(__file__).resolve().parent.parent


class TestRemovedFolderIsGone:
    def _two(self, tmp_path):
        fa = tmp_path / "chipA"
        fb = tmp_path / "chipB"
        _seed_run(fa, 7, qubits=["qa7"])
        _seed_run(fb, 7, qubits=["qb7"])          # the SAME run id in the other folder
        app, c = _app_with_folders(tmp_path, [fa, fb])
        c.get("/datasets", headers=HX)             # the table the user is looking at
        with app.app_context():
            ka, kb = routes._folder_key(fa), routes._folder_key(fb)
        return app, c, fa, fb, ka, kb

    def test_a_removed_folders_uid_404s_instead_of_opening_the_other_folders_run(self, tmp_path):
        app, c, fa, fb, ka, kb = self._two(tmp_path)
        before = c.get(f"/dataset/{ka}:7", headers=HX)
        assert before.status_code == 200 and "qa7" in before.get_data(as_text=True)
        r = c.post("/workspace/remove", data={"folder": str(fa)}, headers=HX)
        assert r.status_code == 200
        after = c.get(f"/dataset/{ka}:7", headers=HX)
        assert "qb7" not in after.get_data(as_text=True), \
            "the removed folder's uid opened the OTHER folder's run #7"
        assert after.status_code == 404
        # the inspector's down-arrow asks this route: it must not step through
        # the other folder's runs under the removed folder's uid either
        assert c.get(f"/dataset/{ka}:7/neighbor?dir=1", headers=HX).status_code == 404
        # the remaining folder is untouched
        assert c.get(f"/dataset/{kb}:7", headers=HX).status_code == 200

    def test_the_remove_tells_an_open_datasets_page(self, tmp_path):
        app, c, fa, fb, ka, kb = self._two(tmp_path)
        r = c.post("/workspace/remove", data={"folder": str(fa)}, headers=HX)
        trig = json.loads(r.headers.get("HX-Trigger") or "{}")
        assert trig.get("workspaceRootsChanged", {}).get("removed") == [ka]
        # ...and the table it would re-read no longer lists that folder
        body = c.get("/datasets", headers=HX).get_data(as_text=True)
        assert "chipA" not in body.split('id="ds-folders-data"', 1)[1].split("</script>", 1)[0]

    def test_a_re_add_brings_the_folder_back(self, tmp_path):
        app, c, fa, fb, ka, kb = self._two(tmp_path)
        c.post("/workspace/remove", data={"folder": str(fa)}, headers=HX)
        r = c.post("/workspace/add", data={"folder": str(fa)}, headers=HX)
        assert "workspaceRootsChanged" in (r.headers.get("HX-Trigger") or "")
        again = c.get(f"/dataset/{ka}:7", headers=HX)
        assert again.status_code == 200 and "qa7" in again.get_data(as_text=True)
        with app.app_context():
            assert ka not in routes._retired_dataset_keys()

    def test_drift_fallback_still_serves_a_renamed_lone_folder(self, tmp_path):
        """The guard is for an EXPLICIT remove only: a key that merely drifted
        (renamed/moved folder, never removed) still resolves on a lone folder."""
        fa = tmp_path / "chipA"
        _seed_run(fa, 3, qubits=["qa3"])
        app, c = _app_with_folders(tmp_path, [fa])
        c.get("/datasets", headers=HX)
        r = c.get("/dataset/0badc0de:3", headers=HX)
        assert r.status_code == 200 and "qa3" in r.get_data(as_text=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_ds_roots_changed_client_selfcheck():
    proc = subprocess.run(
        ["node", str(_ROOT / "tests" / "ds_roots_changed_selfcheck.cjs")],
        capture_output=True, text=True, cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
