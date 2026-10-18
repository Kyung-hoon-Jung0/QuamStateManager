"""v1.5.1 SB-F9: dataset tags set from two SM processes on one run were lost.

Four tag POSTs 0.4 s apart, alternating two processes, all answered 200; the
file ended with the last two (each process wrote ITS whole list for the run)
and the two processes showed different sets.

Each change is now applied to what is on disk -- add, remove, favorite,
note -- under a lock every SM process on the machine takes, and the store
adopts the merged result. Two DatasetStores on one folder are two
processes' views (separate memory); a spawned process is the real thing.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

from quam_state_manager.core.dataset import FAVORITE_TAG, DatasetStore
from tests.test_multifolder_datasets import _seed_run

ROOT = Path(__file__).resolve().parent.parent


def _on_disk(root: Path) -> dict:
    return json.loads((root / "quashboard_tags.json").read_text(encoding="utf-8"))


def test_alternating_tags_from_two_views_all_stay(tmp_path):
    root = tmp_path / "data"
    _seed_run(root, 1)
    a, b = DatasetStore(root), DatasetStore(root)
    a.add_tag(1, "proc-a")
    b.add_tag(1, "proc-b")
    a.add_tag(1, "proc-c")
    got = b.add_tag(1, "proc-d")
    assert _on_disk(root)["tags"]["1"] == ["proc-a", "proc-b", "proc-c", "proc-d"]
    assert got == ["proc-a", "proc-b", "proc-c", "proc-d"], "the answer hides the other window's tags"
    a.add_tag(1, "proc-e")
    assert a.runs[1].tags == ["proc-a", "proc-b", "proc-c", "proc-d", "proc-e"]


def test_a_removal_is_not_undone_by_the_other_views_next_add(tmp_path):
    root = tmp_path / "data"
    _seed_run(root, 1)
    a, b = DatasetStore(root), DatasetStore(root)
    a.add_tag(1, "keep")
    a.add_tag(1, "drop")
    b.add_tag(1, "x")                       # b now holds keep, drop, x
    a.remove_tag(1, "drop")
    b.add_tag(1, "y")                       # b's memory still had "drop"
    assert _on_disk(root)["tags"]["1"] == ["keep", "x", "y"]
    b.toggle_bookmark(1)
    a.add_tag(1, "z")
    assert _on_disk(root)["tags"]["1"] == ["keep", "x", "y", FAVORITE_TAG, "z"]


def test_notes_and_tags_from_two_views_merge(tmp_path):
    root = tmp_path / "data"
    _seed_run(root, 1)
    _seed_run(root, 2)
    a, b = DatasetStore(root), DatasetStore(root)
    a.set_note(1, "note from a")
    b.add_tag(1, "tag-b")
    b.set_note(2, "note from b")
    a.add_tag(2, "tag-a")
    disk = _on_disk(root)
    assert disk["notes"] == {"1": "note from a", "2": "note from b"}
    assert disk["tags"] == {"1": ["tag-b"], "2": ["tag-a"]}


def test_concurrent_writers_lose_nothing(tmp_path):
    root = tmp_path / "data"
    _seed_run(root, 1)
    a, b = DatasetStore(root), DatasetStore(root)

    def burst(store, prefix):
        for i in range(25):
            store.add_tag(1, f"{prefix}{i}")

    ts = [threading.Thread(target=burst, args=(a, "a")), threading.Thread(target=burst, args=(b, "b"))]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    assert len(_on_disk(root)["tags"]["1"]) == 50


_CHILD = textwrap.dedent(r'''
    import sys
    from pathlib import Path
    from quam_state_manager.core.dataset import DatasetStore
    s = DatasetStore(Path(sys.argv[1]))
    for t in sys.argv[2:]:
        s.add_tag(1, t)
    print("DONE", flush=True)
''')


def test_a_real_second_process(tmp_path):
    root = tmp_path / "data"
    _seed_run(root, 1)
    a = DatasetStore(root)
    a.add_tag(1, "here-1")
    env = dict(os.environ, PYTHONUTF8="1")
    r = subprocess.run([sys.executable, "-c", _CHILD, str(root), "there-1", "there-2"],
                       cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=240)
    assert "DONE" in r.stdout, r.stderr[-3000:]
    a.add_tag(1, "here-2")
    assert _on_disk(root)["tags"]["1"] == ["here-1", "there-1", "there-2", "here-2"]
