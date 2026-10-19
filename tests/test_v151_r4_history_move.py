"""v1.5.1 R4 A1: a moved unnamed chip's history follows a WHOLE-folder move.

R3 M2 handed a history dir over only on evidence of a move: the asking folder
holds captured content, or the holder's immediate parent answers. A chip
folder moved together with its parent (``.../2026/chipA/quam_state`` ->
``.../archive/chipA/quam_state``) after a node wrote state.json met neither,
and the moved chip got a new, empty history for good.

Now the nearest answering ancestor of the holder below its drive/share root
counts too, when the asking folder carries the vanished chain of folder
names. Mere absence still hands nothing over: a drive or share that does not
answer (offline share, unplugged disk, unmapped drive), a folder on the way
up that does not answer, or a vanished folder the asker is not a copy of.
"""
from __future__ import annotations

import os
import string
from pathlib import Path

import pytest

from quam_state_manager.core import folder_probe
from quam_state_manager.core.history import HistoryManager
from tests.test_v151_r2_history_holders import _owners, _write


@pytest.fixture(autouse=True)
def _fresh_probes():
    folder_probe.forget()
    yield
    folder_probe.forget()


def _hm(inst):
    return HistoryManager(inst, max_snapshots=50, cache_size=3)


def _captured(inst, live, n=4):
    hm = _hm(inst)
    for i in range(n):
        _write(live, 1.0e-5 + i * 1e-7)
        hm.check_and_snapshot(live, "manual", force=True)
    hm.claim_history_dir(live)
    return hm._key_for(live)


@pytest.mark.parametrize("moved_level", ["chip", "year"])
def test_a_whole_folder_move_with_changed_content_keeps_the_history(tmp_path, moved_level):
    inst = tmp_path / "inst"
    live = tmp_path / "lab" / "2026" / "chipA" / "quam_state"
    key = _captured(inst, live)
    _write(live, 3.3e-5)                      # a node wrote while SM was closed
    (tmp_path / "lab" / "archive").mkdir()
    if moved_level == "chip":                 # chipA moved, 2026 stays
        os.rename(tmp_path / "lab" / "2026" / "chipA", tmp_path / "lab" / "archive" / "chipA")
        moved = tmp_path / "lab" / "archive" / "chipA" / "quam_state"
    else:                                     # the whole year folder moved
        os.rename(tmp_path / "lab" / "2026", tmp_path / "lab" / "archive" / "2026")
        moved = tmp_path / "lab" / "archive" / "2026" / "chipA" / "quam_state"
    folder_probe.forget()
    hm = _hm(inst)
    hm.claim_history_dir(moved)               # the user opens the moved chip
    assert hm._key_for(moved) == key and len(hm.list_snapshots(moved)) == 4, (
        f"the moved chip lost its history: {_owners(inst)}")


def test_a_vanished_folder_does_not_hand_its_history_to_another_copy(tmp_path):
    """The asker carries a different chain of names: a copy, not the moved
    folder (R3 M2 simulates an offline share this way too)."""
    inst = tmp_path / "inst"
    live = tmp_path / "lab" / "2026" / "chipA" / "quam_state"
    key = _captured(inst, live, n=2)
    _write(live, 3.3e-5)
    os.rename(tmp_path / "lab" / "2026" / "chipA", tmp_path / "lab" / "2026" / "chipA_away")
    other = _write(tmp_path / "backup" / "chipB" / "quam_state", 2.0e-5)
    folder_probe.forget()
    hm = _hm(inst)
    hm.claim_history_dir(other)
    assert hm._key_for(other) != key
    assert _owners(inst).get(key) and "backup" not in _owners(inst)[key], _owners(inst)


def _free_drive():
    if os.name != "nt":
        return None
    for letter in reversed(string.ascii_uppercase[3:]):
        if not os.path.exists(f"{letter}:\\"):
            return f"{letter}:\\"
    return None


@pytest.mark.parametrize("how", ["unmapped drive", "drive does not answer", "folder does not answer"])
def test_no_answer_hands_nothing_over_even_with_the_same_names(tmp_path, monkeypatch, how):
    """The holder's chain of names is carried by the asker, but its drive or
    share is offline / unplugged / unmapped, or a folder on the way up gives
    no answer: absence is not a move."""
    inst = tmp_path / "inst"
    live = tmp_path / "lab" / "2026" / "chipA" / "quam_state"
    key = _captured(inst, live, n=2)
    _write(live, 3.3e-5)
    (tmp_path / "lab" / "archive").mkdir()
    os.rename(tmp_path / "lab" / "2026" / "chipA", tmp_path / "lab" / "archive" / "chipA")
    moved = tmp_path / "lab" / "archive" / "chipA" / "quam_state"
    hm = _hm(inst)
    if how == "unmapped drive":
        drive = _free_drive()
        if drive is None:
            pytest.skip("needs Windows and a free drive letter")
        holder = str(Path(drive) / "lab" / "2026" / "chipA" / "quam_state")
        hm._record_owners({key: holder})       # the chip lived on a drive not mapped now
    else:
        real = folder_probe.folders_present
        # the holder's own parent gives no answer: walking past it would
        # reach 2026 (there) with a chain the moved folder carries
        silent = (Path(live.anchor) if how == "drive does not answer" else live.parent)

        def present(paths, **kw):
            out = real([p for p in paths if Path(p) != silent], **kw)
            out.update({str(p): None for p in paths if Path(p) == silent})
            return out
        monkeypatch.setattr(folder_probe, "folders_present", present)
    folder_probe.forget()
    up = _hm(inst)
    up.claim_history_dir(moved)
    assert up._key_for(moved) != key, f"absence was read as a move ({how}): {_owners(inst)}"
