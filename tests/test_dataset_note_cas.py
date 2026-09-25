"""datasets-r2-12: a run's note is never silently lost.

Two windows (or two tabs) editing the same run's note used to be last-write-
wins: the stale tab's save replaced the other's text and neither was told. And
text typed into the note was lost on F5 (the save ran only on blur, which a
reload never fires). The server now does a compare-and-swap on ``expected``
(the note the editor started from); the client sends it, honours the 409, and
flushes an unsaved note on ``pagehide`` with a keepalive request -- pinned
against the real app.js by ``tests/dataset_note_selfcheck.cjs``.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core.dataset import DatasetStore
from quam_state_manager.web import routes
from tests.test_multifolder_datasets import _app_with_folders, _seed_run

_ROOT = Path(__file__).resolve().parents[1]


def _notes_on_disk(root: Path) -> dict:
    p = root / "quashboard_tags.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8")).get("notes", {})


class TestStoreCompareAndSwap:
    def test_a_stale_expected_is_refused_and_the_file_is_unchanged(self, tmp_path):
        root = tmp_path / "data"
        _seed_run(root, 1)
        s = DatasetStore(root)
        s.set_note(1, "tabA", expected="")
        with pytest.raises(DatasetStore.NoteConflict) as ei:
            s.set_note(1, " tabB", expected="")     # the tab that still shows ""
        assert ei.value.current == "tabA"
        assert _notes_on_disk(root) == {"1": "tabA"}

    def test_a_matching_expected_saves(self, tmp_path):
        root = tmp_path / "data"
        _seed_run(root, 1)
        s = DatasetStore(root)
        s.set_note(1, "first", expected="")
        s.set_note(1, "second", expected="first")
        assert _notes_on_disk(root) == {"1": "second"}

    def test_no_expected_keeps_last_write_wins(self, tmp_path):
        root = tmp_path / "data"
        _seed_run(root, 1)
        s = DatasetStore(root)
        s.set_note(1, "a")
        s.set_note(1, "b")
        assert _notes_on_disk(root) == {"1": "b"}

    def test_the_same_text_twice_is_not_a_conflict(self, tmp_path):
        """A blur-save and the pagehide flush can both carry the same text."""
        root = tmp_path / "data"
        _seed_run(root, 1)
        s = DatasetStore(root)
        s.set_note(1, "typed", expected="")
        s.set_note(1, "typed", expected="")
        assert _notes_on_disk(root) == {"1": "typed"}

    def test_another_window_on_the_same_folder_is_seen(self, tmp_path):
        """Two SM processes = two stores; the CAS reads the file, not memory."""
        root = tmp_path / "data"
        _seed_run(root, 1)
        a, b = DatasetStore(root), DatasetStore(root)
        a.set_note(1, "from A", expected="")
        with pytest.raises(DatasetStore.NoteConflict) as ei:
            b.set_note(1, "from B", expected="")
        assert ei.value.current == "from A"
        assert _notes_on_disk(root) == {"1": "from A"}
        b.set_note(1, "from B", expected="from A")   # the informed overwrite
        assert _notes_on_disk(root) == {"1": "from B"}


class TestRoute:
    def _setup(self, tmp_path):
        f = tmp_path / "data"
        _seed_run(f, 1)
        app, c = _app_with_folders(tmp_path, [f])
        with app.app_context():
            key = routes._folder_key(f)
        return f, c, f"{key}:1"

    def test_the_route_answers_a_stale_tab_with_409(self, tmp_path):
        f, c, uid = self._setup(tmp_path)
        r = c.post(f"/dataset/{uid}/note", json={"note": "tabA", "expected": ""})
        assert r.status_code == 200
        r = c.post(f"/dataset/{uid}/note", json={"note": " tabB", "expected": ""})
        assert r.status_code == 409
        d = r.get_json()
        assert d["note_conflict"] is True and d["current"] == "tabA"
        assert _notes_on_disk(f) == {"1": "tabA"}
        r = c.post(f"/dataset/{uid}/note", json={"note": " tabB", "expected": "tabA"})
        assert r.status_code == 200
        assert _notes_on_disk(f) == {"1": " tabB"}

    def test_a_body_without_expected_still_saves(self, tmp_path):
        f, c, uid = self._setup(tmp_path)
        c.post(f"/dataset/{uid}/note", json={"note": "x"})
        r = c.post(f"/dataset/{uid}/note", json={"note": "y"})
        assert r.status_code == 200
        assert _notes_on_disk(f) == {"1": "y"}

    def test_the_editor_carries_its_baseline_and_saves_on_escape(self, tmp_path):
        f, c, uid = self._setup(tmp_path)
        c.post(f"/dataset/{uid}/note", json={"note": 'say "hi"'})
        html = c.get(f"/dataset/{uid}", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'data-saved="say &#34;hi&#34;"' in html or 'data-saved="say &quot;hi&quot;"' in html
        assert f'data-uid="{uid}"' in html
        assert "if (event.key === 'Escape') this.blur();" in html


_SELFCHECK = _ROOT / "tests" / "dataset_note_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_note_client_selfcheck():
    proc = subprocess.run(["node", str(_SELFCHECK)], capture_output=True, text=True,
                          cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
