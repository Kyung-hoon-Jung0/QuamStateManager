"""v1.5.1 R3 M7: unapplied work is copied to another folder only on evidence
of a move -- and the offer says "if this is the same chip".

``synced_live_hash`` equality cannot tell a moved folder from an identical
independent copy, nor from the same folder under another spelling (a mapped
drive letter vs the UNC path). The original's share offline, a same-content
copy opened meanwhile was offered the work, and restoring it there and in
the original applied the same edits in two places. The old folder's drive or
share must answer now as well, and the offer and its sync-panel row say to
restore only if this is the same chip.
"""
from __future__ import annotations

import json
import pathlib

from quam_state_manager.core import pending_tray, safe_io, working_copy
from tests.test_v151_r2_moved_work import (  # noqa: F401
    _chip, _fresh_probes, _listed, _note, _open, staged_x)

UNC = r"\\nonexistent-host-r3\share\chipX\quam_state"


def _old_folder_on_a_share(t, monkeypatch):
    """The original copy was recorded on a share that does not answer now
    (exists() reads an unreachable share as "not found")."""
    root = working_copy.working_state_root(t["inst"])
    meta_p = root / f"{t['x_key']}.meta.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    meta["live_folder"] = UNC
    safe_io.atomic_write_json(meta_p, meta)
    real = pathlib.Path.exists

    def exists(self, *a, **k):
        if str(self).lower().startswith(r"\\nonexistent-host-r3"):
            return False
        return real(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "exists", exists)


def test_a_same_content_copy_beside_an_offline_share_is_offered_nothing(staged_x, monkeypatch):
    t = staged_x
    _chip(t["y"])                                  # a same-design copy, same content
    _old_folder_on_a_share(t, monkeypatch)
    _app, _c, cy = _open(t["inst"], t["y"])
    assert _listed(cy) == [], f"the offline original's work was copied: {_listed(cy)}"
    note = _note(cy)
    assert UNC in note and "nothing was brought here" in note, note
    root = working_copy.working_state_root(t["inst"])
    assert not (root / f"{t['x_key']}.pending_tray.moved-offer.json").exists()


def test_a_moved_folder_is_offered_its_work_if_this_is_the_same_chip(staged_x):
    """The other half: the drive answers, the content is the old sync point:
    the work is copied, and the offer says to restore only if this is the
    same chip."""
    import os
    t = staged_x
    os.replace(t["tmp"] / "share", t["tmp"] / "moved")
    moved = t["tmp"] / "moved" / "chipX" / "quam_state"
    _app, _c, cm = _open(t["inst"], moved)
    assert _listed(cm) == [["qubits.q1.T1"]], _listed(cm)
    assert "if this is the same chip" in _note(cm), _note(cm)
    reasons = [d["reason"] for d in pending_tray.unrecovered(cm["working_copy"])]
    assert reasons and all("if this is the same chip" in r for r in reasons), reasons
