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
    r = W.save_from_pane(inst, "c", "FlatPulse", edited, base, region_ok=True)
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
    assert c["file_sha"] == D.sha256_bytes(outside)
    k = W.save_from_pane(inst, "c", "FlatPulse", mine, base, keep="file", file_sha=c["file_sha"])
    assert k["kept"] == "file" and k["text"].encode() == outside
    assert D.load_draft(inst, "c", "FlatPulse") == outside
    k = W.save_from_pane(inst, "c", "FlatPulse", mine, base, keep="sm", file_sha=c["file_sha"],
                         region_ok=True)
    assert k["kept"] == "sm" and D.load_draft(inst, "c", "FlatPulse") == mine.encode()


def _previewed(inst, cls="FlatPulse"):
    """A good preview of the current draft bytes (the lab env stand-in)."""
    def run(py, req, timeout):
        data = open(req["draft"], "rb").read()
        return {"ok": True, "samples": [0.0], "n": 1, "fields": [], "sha256": D.sha256_bytes(data)}
    assert D.preview("py", inst, "c", cls, runner=run)["ok"]


def test_three_phrases_by_state(inst, tmp_path):
    src = T.render(_spec())
    D.save_draft(inst, "c", "FlatPulse", src)
    assert W.status(inst, "c", "FlatPulse")["line"] == "Draft, not saved"
    target = tmp_path / "quam_config" / "flat_pulse.py"
    target.parent.mkdir()
    _previewed(inst)
    st = W.save_to_target(inst, "c", "FlatPulse", str(target))
    assert st["line"] == f"Saved to {target}, same as draft"
    assert target.read_bytes() == src.encode()
    D.save_draft(inst, "c", "FlatPulse", T.render(_spec(0.9)))
    assert W.status(inst, "c", "FlatPulse")["line"] == f"Saved to {target}, differs from draft"
    target.unlink()
    assert W.status(inst, "c", "FlatPulse")["line"] == "Draft, not saved"


def test_save_to_target_refuses_bad_paths(inst, tmp_path):
    D.save_draft(inst, "c", "FlatPulse", T.render(_spec()))
    _previewed(inst)
    with pytest.raises(D.DraftError):
        W.save_to_target(inst, "c", "FlatPulse", str(tmp_path / "x.txt"))
    with pytest.raises(D.DraftError):
        W.save_to_target(inst, "c", "FlatPulse", str(tmp_path / "nope" / "x.py"))


# Review round 1 fixes

def _draft(inst, default=0.1):
    src = T.render(_spec(default))
    D.save_draft(inst, "c", "FlatPulse", src)
    W.watch_for(inst, "c", "FlatPulse").adopt(src.encode())
    return src, D.sha256_bytes(src.encode())


def test_save_to_file_over_an_existing_file_asks_first_and_keeps_a_copy(inst, tmp_path):
    src, _ = _draft(inst)
    _previewed(inst)
    qc = tmp_path / "qc"
    qc.mkdir()
    t = qc / "existing.py"
    t.write_bytes(b"# a module of the lab\n")
    r = W.save_to_target(inst, "c", "FlatPulse", str(t))
    assert not r["ok"] and r["confirm"]["path"] == str(t)
    assert r["confirm"]["question"] == W.REPLACE_QUESTION and str(t) in r["confirm"]["loses"]
    assert t.read_bytes() == b"# a module of the lab\n"          # nothing written
    assert W.get_target(inst, "c", "FlatPulse") is None
    assert list(qc.iterdir()) == [t]
    r = W.save_to_target(inst, "c", "FlatPulse", str(t), replace_sha="0" * 64)
    assert not r["ok"] and "confirm" in r                         # a confirmation of other bytes
    t.write_bytes(b"# moved again\n")
    r2 = W.save_to_target(inst, "c", "FlatPulse", str(t), replace_sha=r["confirm"]["file_sha"])
    assert not r2["ok"] and t.read_bytes() == b"# moved again\n"  # it moved after the question
    r3 = W.save_to_target(inst, "c", "FlatPulse", str(t), replace_sha=r2["confirm"]["file_sha"])
    assert r3["ok"] and t.read_bytes() == src.encode()
    bak = r3["backup"]
    assert bak and os.path.dirname(bak) == str(qc) and bak.endswith(".bak")
    assert open(bak, "rb").read() == b"# moved again\n"


def test_save_to_file_again_over_its_own_last_save_needs_no_question(inst, tmp_path):
    _draft(inst)
    _previewed(inst)
    t = tmp_path / "flat_pulse.py"
    assert W.save_to_target(inst, "c", "FlatPulse", str(t))["backup"] is None   # a new file
    assert W.save_to_target(inst, "c", "FlatPulse", str(t))["backup"] is None   # same bytes
    new = T.render(_spec(0.5))
    D.save_draft(inst, "c", "FlatPulse", new)
    _previewed(inst)
    r = W.save_to_target(inst, "c", "FlatPulse", str(t))
    assert r["ok"] and t.read_bytes() == new.encode() and r["backup"]
    t.write_bytes(b"# edited outside SM\n")                       # no longer SM's own save
    assert "confirm" in W.save_to_target(inst, "c", "FlatPulse", str(t))


def test_keep_sm_rechecks_the_file_it_was_asked_about(inst):
    src, base = _draft(inst)
    path = D.draft_path(inst, "c", "FlatPulse")
    first = src.replace("# Edit freely", "# first outside edit").encode()
    assert first != src.encode()
    path.write_bytes(first)
    c = W.save_from_pane(inst, "c", "FlatPulse", src + "# mine\n", base)["conflict"]
    second = src.replace("# Edit freely", "# second outside edit").encode()
    path.write_bytes(second)                                       # after the dialog opened
    r = W.save_from_pane(inst, "c", "FlatPulse", src + "# mine\n", base, keep="sm",
                         file_sha=c["file_sha"])
    assert not r["ok"] and r["conflict"]["file_sha"] == D.sha256_bytes(second)
    assert path.read_bytes() == second
    r = W.save_from_pane(inst, "c", "FlatPulse", src + "# mine\n", base, keep="sm")
    assert not r["ok"] and "conflict" in r and path.read_bytes() == second   # no sha: ask again
    r = W.save_from_pane(inst, "c", "FlatPulse", src + "# mine\n", base, keep="file",
                         file_sha=c["file_sha"])
    assert not r["ok"] and "conflict" in r
    r = W.save_from_pane(inst, "c", "FlatPulse", src + "# mine\n", base, keep="sm",
                         file_sha=D.sha256_bytes(second))
    assert r["ok"] and path.read_bytes() == (src + "# mine\n").encode()


def test_settled_read_waits_for_two_equal_looks(monkeypatch, tmp_path):
    p = tmp_path / "f.py"
    p.write_bytes(b"abc")
    st = W.file_stat(p)
    seq = iter([(1, 1), (2, 2), (3, 3), st, st, st])     # still growing, then settled
    monkeypatch.setattr(W, "file_stat", lambda path: next(seq))
    assert W.settled_read(p, pause=0) == b"abc"
    seq2 = iter([(1, 1), (2, 2), (3, 3), (4, 4), (5, 5)])
    monkeypatch.setattr(W, "file_stat", lambda path: next(seq2))
    assert W.settled_read(p, pause=0, tries=4) is None      # never settled: not read


def test_keep_file_never_adopts_a_file_still_being_written(inst, monkeypatch):
    src, base = _draft(inst)
    path = D.draft_path(inst, "c", "FlatPulse")
    path.write_bytes(src.encode()[:40])
    monkeypatch.setattr(W, "settled_read", lambda p: None)
    r = W.save_from_pane(inst, "c", "FlatPulse", src, base, keep="file",
                         file_sha=D.sha256_bytes(src.encode()[:40]))
    assert not r["ok"] and "still being written" in r["error"]
    assert W.watch_for(inst, "c", "FlatPulse").text == src


def test_pane_save_may_not_change_the_template_region(inst):
    src, base = _draft(inst)
    path = D.draft_path(inst, "c", "FlatPulse")
    changed = T.render(_spec(0.7))
    r = W.save_from_pane(inst, "c", "FlatPulse", changed, base)
    assert not r["ok"] and r["region"] and "'amplitude'" in r["error"]
    assert path.read_bytes() == src.encode()
    broken = src.replace(T.REGION_END, T.REGION_END + "X")
    r = W.save_from_pane(inst, "c", "FlatPulse", broken, base)
    assert not r["ok"] and "markers" in r["error"] and path.read_bytes() == src.encode()
    other = T.render(T.ClassSpec("FlatPulse", _spec().fields, "amplitude * np.ones(n) * 2"))
    r = W.save_from_pane(inst, "c", "FlatPulse", other, base)
    assert not r["ok"] and "formula" in r["error"]
    r = W.save_from_pane(inst, "c", "FlatPulse", changed, base, region_ok=True)   # unlocked
    assert r["ok"] and path.read_bytes() == changed.encode()


def test_pane_save_outside_the_region_keeps_the_region_bytes(inst):
    src, base = _draft(inst)
    mine = src + "\n# a helper the user wrote\n"
    r = W.save_from_pane(inst, "c", "FlatPulse", mine, base)
    assert r["ok"]
    a, b = W._describe(src)["protect"]
    data = D.load_draft(inst, "c", "FlatPulse").decode()
    assert data[a:b] == src[a:b] and data == mine


@pytest.mark.parametrize("missing", [True, False], ids=["missing-file-200", "existing-file-409"])
def test_unknown_base_after_restart_respects_file_presence(inst, monkeypatch, missing):
    from flask import Flask
    from quam_state_manager.web import pulse_class_page as P

    src, base = _draft(inst)
    path = D.draft_path(inst, "c", "FlatPulse")
    mine = src + "\n# helper from the pane\n"
    outside = T.render(_spec(0.4)).encode()
    if missing:
        path.unlink()
    else:
        path.write_bytes(outside)
    W._WATCHES.clear()  # Restart loses the pane's base, even if a poll adopts the disk.
    monkeypatch.setattr(P, "_inst", lambda: inst)
    monkeypatch.setattr(P, "_scope", lambda: "c")
    app = Flask(__name__)
    app.add_url_rule("/pulse-class/save", view_func=P.pulse_class_save, methods=["POST"])
    client = app.test_client()
    body = {"cls": "FlatPulse", "text": mine, "base_sha": base}
    if not missing:
        conflict = client.post("/pulse-class/save", json=body)
        assert conflict.status_code == 409
        body.update(keep="sm", file_sha=conflict.get_json()["conflict"]["file_sha"])
    response = client.post("/pulse-class/save", json=body)
    result = response.get_json()
    if missing:
        assert response.status_code == 200
        assert result["ok"] and result["text"] == mine
        assert path.read_bytes() == mine.encode()
    else:
        assert response.status_code == 409
        assert not result["ok"] and result["base_unknown"] and "conflict" not in result
        assert result["error"] == (
            "The template base is no longer known. To save these lines, press "
            "\"Edit the template lines by hand\" first, then Save draft.")
        assert path.read_bytes() == outside


def test_save_to_file_refuses_a_relative_path_and_stores_absolute(inst, tmp_path, monkeypatch):
    _draft(inst)
    _previewed(inst)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(D.DraftError, match="full path"):
        W.save_to_target(inst, "c", "FlatPulse", "rel_pulse.py")
    assert not (tmp_path / "rel_pulse.py").exists()
    folder = D.draft_path(inst, "c", "FlatPulse").parent
    (folder / W.TARGET_FILE).write_text('{"path": "rel_pulse.py"}', encoding="utf-8")
    assert W.get_target(inst, "c", "FlatPulse") is None
    t = tmp_path / "abs_pulse.py"
    W.save_to_target(inst, "c", "FlatPulse", str(t))
    assert W.get_target(inst, "c", "FlatPulse") == str(t)


def test_save_to_file_refuses_a_draft_that_does_not_parse_or_import(inst, tmp_path):
    t = tmp_path / "flat_pulse.py"
    D.save_draft(inst, "c", "FlatPulse", "class FlatPulse:\n    def (:\n")
    with pytest.raises(D.DraftError, match="line 2"):
        W.save_to_target(inst, "c", "FlatPulse", str(t))
    assert not t.exists()
    _draft(inst)
    with pytest.raises(D.DraftError, match="not imported"):      # never previewed
        W.save_to_target(inst, "c", "FlatPulse", str(t))
    _previewed(inst)
    D.save_draft(inst, "c", "FlatPulse", T.render(_spec(0.3)))      # changed after the preview
    with pytest.raises(D.DraftError, match="not imported"):
        W.save_to_target(inst, "c", "FlatPulse", str(t))
    assert not t.exists()
    _previewed(inst)
    assert W.save_to_target(inst, "c", "FlatPulse", str(t))["ok"]


@pytest.mark.parametrize("name", ["CON.py", "nul.py", "COM1.py", "aux.py", "Lpt9.x.py"])
def test_save_to_file_refuses_windows_device_names(inst, tmp_path, name):
    _draft(inst)
    _previewed(inst)
    with pytest.raises(D.DraftError, match="reserved Windows device name"):
        W.save_to_target(inst, "c", "FlatPulse", str(tmp_path / name))


def test_save_to_file_refuses_a_folder_named_like_a_file(inst, tmp_path):
    _draft(inst)
    _previewed(inst)
    (tmp_path / "x.py").mkdir()
    with pytest.raises(D.DraftError, match="is a folder"):
        W.save_to_target(inst, "c", "FlatPulse", str(tmp_path / "x.py"))


def test_status_names_a_deleted_draft(inst, tmp_path):
    _draft(inst)
    _previewed(inst)
    t = tmp_path / "flat_pulse.py"
    W.save_to_target(inst, "c", "FlatPulse", str(t))
    path = D.draft_path(inst, "c", "FlatPulse")
    path.unlink()
    st = W.status(inst, "c", "FlatPulse")
    assert st["state"] == D.STATE_MISSING and st["line"] == f"The draft file is gone: {path}"
    assert "Saved to" not in st["line"]
