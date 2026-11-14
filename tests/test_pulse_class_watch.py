"""Draft watching, the settle rule, the two-editor conflict and the status phrases."""
from __future__ import annotations

import os
import time

import pytest

from quam_state_manager.core import pulse_class_drafts as D
from quam_state_manager.core import pulse_class_template as T
from quam_state_manager.core import pulse_class_watch as W


def _spec(default=0.1):
    return T.ClassSpec("FlatPulse", [T.FieldSpec("amplitude", "float", default, "V"),
                                     T.FieldSpec("length", "int", 100, "ns")],
                       "amplitude * np.ones(n)")


@pytest.fixture
def inst(tmp_path):
    W._WATCHES.clear()
    return tmp_path / "instance"


def _bump(path, data):
    """Write *data* and move the mtime forward so coarse clocks still see it."""
    path.write_bytes(data)
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))


def test_settle_rule_never_parses_a_partial_write(inst, monkeypatch):
    src = T.render(_spec()).encode()
    D.save_draft(inst, "c", "FlatPulse", src)
    w = W.watch_for(inst, "c", "FlatPulse")
    parsed = []
    real = T.parse
    monkeypatch.setattr(T, "parse", lambda text: (parsed.append(text), real(text))[1])
    new = T.render(_spec(0.25)).encode()
    path = D.draft_path(inst, "c", "FlatPulse")
    half = new[: len(new) // 2]
    _bump(path, half)                       # part one of an outside save
    assert w.poll()["status"] == "settling"
    time.sleep(0.05)                        # the writer pauses
    with open(path, "ab") as f:             # part two
        f.write(new[len(half):])
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 20_000_000))
    assert w.poll()["status"] == "settling"
    r = w.poll()
    assert r["status"] == "changed" and r["text"].encode() == new
    assert r["info"]["spec"]["fields"][0]["default"] == 0.25
    assert parsed == [new.decode()]         # the half file never reached the parser
    assert w.poll()["status"] == "unchanged"


def test_a_write_during_the_read_is_not_taken(inst, monkeypatch):
    D.save_draft(inst, "c", "FlatPulse", T.render(_spec()))
    w = W.watch_for(inst, "c", "FlatPulse")
    path = D.draft_path(inst, "c", "FlatPulse")
    _bump(path, b"class FlatPulse:\n")
    assert w.poll()["status"] == "settling"
    stats = iter([(17, 1), (99, 2)])        # the stat moves between read and re-check
    real = W.file_stat
    calls = {"n": 0}

    def fake(p):
        calls["n"] += 1
        return next(stats, None) or real(p)
    monkeypatch.setattr(W, "file_stat", fake)
    w._seen = (17, 1)
    assert w.poll()["status"] == "settling"
    assert w.text == T.render(_spec())


def test_in_sm_save_writes_the_file_and_an_outside_edit_is_seen(inst):
    src = T.render(_spec())
    D.save_draft(inst, "c", "FlatPulse", src)
    base = D.sha256_bytes(src.encode())
    edited = T.render(_spec(0.3))
    r = W.save_from_pane(inst, "c", "FlatPulse", edited, base)
    assert r["ok"] and D.load_draft(inst, "c", "FlatPulse") == edited.encode()
    w = W.watch_for(inst, "c", "FlatPulse")
    assert w.poll()["status"] == "unchanged"     # SM's own write is not an outside edit
    outside = T.render(_spec(0.4)).encode()
    _bump(D.draft_path(inst, "c", "FlatPulse"), outside)
    assert w.poll()["status"] == "settling"
    r = w.poll()
    assert r["status"] == "changed" and r["info"]["spec"]["fields"][0]["default"] == 0.4


def test_conflict_names_the_lost_side_and_each_choice_keeps_exactly_that_side(inst):
    src = T.render(_spec())
    D.save_draft(inst, "c", "FlatPulse", src)
    base = D.sha256_bytes(src.encode())
    outside = T.render(_spec(0.4)).encode()
    D.draft_path(inst, "c", "FlatPulse").write_bytes(outside)
    mine = T.render(_spec(0.7))
    r = W.save_from_pane(inst, "c", "FlatPulse", mine, base)
    assert not r["ok"]
    c = r["conflict"]
    path = str(D.draft_path(inst, "c", "FlatPulse"))
    assert c["question"] == W.CONFLICT_QUESTION
    by = {x["keep"]: x for x in c["choices"]}
    assert by["sm"]["loses"] == f"The outside edit in {path} will be lost."
    assert by["file"]["loses"] == "Your unsaved changes in this editor will be lost."
    assert D.load_draft(inst, "c", "FlatPulse") == outside          # nothing written yet
    k = W.save_from_pane(inst, "c", "FlatPulse", mine, base, keep="file")
    assert k["kept"] == "file" and k["text"].encode() == outside
    assert D.load_draft(inst, "c", "FlatPulse") == outside
    k = W.save_from_pane(inst, "c", "FlatPulse", mine, base, keep="sm")
    assert k["kept"] == "sm" and D.load_draft(inst, "c", "FlatPulse") == mine.encode()


def test_three_phrases_by_state(inst, tmp_path):
    src = T.render(_spec())
    D.save_draft(inst, "c", "FlatPulse", src)
    assert W.status(inst, "c", "FlatPulse")["line"] == "Draft, not saved"
    target = tmp_path / "quam_config" / "flat_pulse.py"
    target.parent.mkdir()
    st = W.save_to_target(inst, "c", "FlatPulse", str(target))
    assert st["line"] == f"Saved to {target}, same as draft"
    assert target.read_bytes() == src.encode()
    D.save_draft(inst, "c", "FlatPulse", T.render(_spec(0.9)))
    assert W.status(inst, "c", "FlatPulse")["line"] == f"Saved to {target}, differs from draft"
    target.unlink()
    assert W.status(inst, "c", "FlatPulse")["line"] == "Draft, not saved"


def test_save_to_target_refuses_bad_paths(inst, tmp_path):
    D.save_draft(inst, "c", "FlatPulse", T.render(_spec()))
    with pytest.raises(D.DraftError):
        W.save_to_target(inst, "c", "FlatPulse", str(tmp_path / "x.txt"))
    with pytest.raises(D.DraftError):
        W.save_to_target(inst, "c", "FlatPulse", str(tmp_path / "nope" / "x.py"))
