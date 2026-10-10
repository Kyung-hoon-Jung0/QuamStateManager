"""v1.3.0 B1 P2: after SM is killed during an Apply, the tray says what is true.

Killing the server mid-Apply (12 trials on a real chip copy) left the edit
either on the live chip (the Apply finished its live write) or kept in the
working copy (staged) -- never lost. Yet every restart said "Staged edits could
not be recovered ... No rows were restored" and kept one more
``pending_tray.unrecovered-*.json`` (11 piled up in one session). Now a refused
checkpoint whose rows are proven (by content hash, both directions) to be in
the working files says where they are, as information, and its checkpoint is
dropped; a checkpoint whose rows are really gone keeps the old error and its
file -- and only the newest few such files are kept."""
import copy
import json
import os
import time
from types import SimpleNamespace

from quam_state_manager.core import pending_tray, safe_io, working_copy
from quam_state_manager.core.loader import ChangeEntry, QuamStore

BASE = {"value": 1, "other": 2}
STAGED = {"value": 3, "other": 4}


def _checkpoint(tmp_path):
    live, folder = tmp_path / "live", tmp_path / "working"
    for target in (live, folder):
        target.mkdir()
        safe_io.write_state_wiring(target, copy.deepcopy(BASE), {})
    wc = SimpleNamespace(live_folder=live, working_folder=folder, key="chip",
                         synced_live_hash=working_copy.content_hash(BASE, {}))
    staged = QuamStore.from_dicts(copy.deepcopy(STAGED), {})
    staged.change_log = [ChangeEntry("value", 1, 3, "state"),
                         ChangeEntry("other", 2, 4, "state")]
    ctx = {"store": staged, "working_copy": wc, "origin": "live",
           "working_dirty": False, "pending_reapply": None}
    pending_tray.checkpoint(ctx)
    return ctx, pending_tray.sidecar_path(wc)


def _cut_after_save(ctx, live_too):
    """The Apply saved the rows into the working files (and, with *live_too*,
    into the live chip and advanced the sync point) before SM was killed."""
    wc = ctx["working_copy"]
    safe_io.write_state_wiring(wc.working_folder, copy.deepcopy(STAGED), {})
    if live_too:
        safe_io.write_state_wiring(wc.live_folder, copy.deepcopy(STAGED), {})
        wc.synced_live_hash = working_copy.content_hash(STAGED, {})
    else:
        wc.synced_live_hash = "moved-by-the-cut-apply"
    ctx["store"] = QuamStore.from_dicts(copy.deepcopy(STAGED), {})
    ctx["wiring_json"] = "{}"


def test_rows_already_on_the_live_chip_are_named_as_such(tmp_path):
    ctx, path = _checkpoint(tmp_path)
    _cut_after_save(ctx, live_too=True)
    pending_tray.attach(ctx)
    note = ctx["_tray_recovery_notice"]
    assert isinstance(note, dict) and note["level"] == "info", note
    assert "already reached the live chip" in note["text"]
    assert "value" in note["text"] and "other" in note["text"]
    assert "could not be recovered" not in note["text"]
    assert not path.exists()
    assert not list(tmp_path.glob("**/*.unrecovered-*.json"))
    assert ctx["store"].state == STAGED            # what the files hold, untouched


def test_rows_kept_in_the_working_copy_are_named_as_staged(tmp_path):
    ctx, path = _checkpoint(tmp_path)
    _cut_after_save(ctx, live_too=False)
    pending_tray.attach(ctx)
    note = ctx["_tray_recovery_notice"]
    assert isinstance(note, dict) and note["level"] == "info", note
    assert "kept in the working copy" in note["text"] and "not written to the live chip" in note["text"]
    assert not path.exists()
    assert not list(tmp_path.glob("**/*.unrecovered-*.json"))


def test_rows_that_are_not_on_disk_keep_the_error_and_the_file(tmp_path):
    ctx, path = _checkpoint(tmp_path)
    ctx["store"] = QuamStore.from_dicts(copy.deepcopy(BASE), {})    # the rows never reached the files
    ctx["wiring_json"] = "{}"
    ctx["working_copy"].synced_live_hash = "new-sync-point"
    pending_tray.attach(ctx)
    assert isinstance(ctx["_tray_recovery_notice"], str)
    assert "No rows were restored" in ctx["_tray_recovery_notice"]
    assert len(list(path.parent.glob("*.unrecovered-*.json"))) == 1


def test_only_the_newest_refused_files_are_kept(tmp_path):
    ctx, path = _checkpoint(tmp_path)
    now = time.time()
    for i in range(7):
        old = path.parent / f"{path.stem}.unrecovered-old{i}.json"
        old.write_text("{}", encoding="utf-8")
        os.utime(old, (now - 1000 + i, now - 1000 + i))
    ctx["store"] = QuamStore.from_dicts(copy.deepcopy(BASE), {})
    ctx["wiring_json"] = "{}"
    ctx["working_copy"].synced_live_hash = "new-sync-point"
    pending_tray.attach(ctx)
    kept = sorted(f.name for f in path.parent.glob("*.unrecovered-*.json"))
    assert len(kept) == pending_tray.KEEP_UNRECOVERED, kept
    assert not any(n.endswith(("old0.json", "old1.json", "old2.json")) for n in kept), kept
    # the one this restart kept -- the one its notice names -- is among them
    assert any(n in ctx["_tray_recovery_notice"] for n in kept)


def test_the_tray_renders_the_information_without_an_error_role(tmp_path):
    from flask import Flask, render_template_string
    app = Flask(__name__, template_folder=str(
        __import__("pathlib").Path(pending_tray.__file__).resolve().parents[1] / "web" / "templates"))
    tpl = ('{% if tray_recovery_notice is defined %}{% set _recovery_notice = tray_recovery_notice() %}'
           '{% if _recovery_notice is mapping %}<div role="status" class="tray-recovery-notice '
           'tray-recovery-{{ _recovery_notice.level }}">{{ _recovery_notice.text }}</div>'
           '{% elif _recovery_notice %}<div role="alert" class="status-error tray-recovery-notice '
           'tray-recovery-error">{{ _recovery_notice }}</div>{% endif %}{% endif %}')
    src = (__import__("pathlib").Path(app.template_folder) / "_pending_tray.html").read_text(encoding="utf-8")
    assert tpl in src, "the tray template's notice line changed; update this pin with it"
    with app.app_context():
        info = render_template_string(tpl, tray_recovery_notice=lambda: {"level": "info", "text": "on live"})
        err = render_template_string(tpl, tray_recovery_notice=lambda: "lost")
    assert 'role="status"' in info and "tray-recovery-info" in info and "status-error" not in info
    assert 'role="alert"' in err and "status-error" in err
