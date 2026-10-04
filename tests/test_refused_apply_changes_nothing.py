"""docs/255 -- a refused apply changes nothing; an mtime-only touch is never stale.

Campaign findings (D:\\work\\sm_qa_rigs\\agent\\FINDINGS.md, stream D):

* D-03 -- an identical-content touch of the live files (mtime only, same
  bytes) left every apply / approve refused ``stale_live``, while
  ``live_diverged`` was false and ``take_live`` said "already matches": no
  door out.
* D-04 -- a refused MCP ``apply_to_live`` left the agent's value in SM's
  working copy with the tray empty and ``live_diverged`` false: SM held
  something live did not and nothing said so.
* A-11 -- a ``stale_live`` refusal still emptied the tray (the door saved
  before it checked) and the undo journal gained a row for a write that never
  happened.
* P3 -- a refused approve left int->float drift in the working copy, which the
  next apply carried to live.

The contract pinned here, on real files in ``tmp_path``:

1. the gate refuses on the question it exists for (docs/116): has the live
   CONTENT moved away from the sync point? A touch, or a re-save of the same
   content, is not a conflict -- at the top of the door and at the tight
   re-check right before the write;
2. a refused press changes NOTHING in SM: the tray (change log), the working
   copy (bytes and mtimes), the undo journal, the re-apply stash, the dirty
   flag -- and never the live files. It names why, and what SM holds that live
   does not stays visible (the tray, ``live_diverged``);
3. this holds also when the refusal can only be seen AFTER the save (a write
   racing the press): the save is put back;
4. the refused edit is still resolvable through every door the panel offers.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from quam_state_manager.core import undo_journal, working_copy
from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app
from quam_state_manager.core import hub as _hub_unrec

_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}
AGENT = {"X-SM-Agent": "claude"}
JSON = {"Accept": "application/json"}


def _state(f01=5.0e9, t1=2.0e-5, rf=7126044234):
    return {
        "qubits": {"qA1": {"id": "qA1", "f_01": f01, "T1": t1,
                           "resonator": {"RF_frequency": rf}}},
        "qubit_pairs": {},
        "active_qubit_names": ["qA1"],
    }


def _write_chip(folder: Path, state: dict, *, indent=None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state, indent=indent), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING, indent=indent), encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state())
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live, "tmp": tmp_path}


def _ctx(env):
    with env["app"].app_context():
        return routes_mod._active_ctx()


def _sha(folder: Path) -> tuple[str, str]:
    return tuple(hashlib.sha256((folder / n).read_bytes()).hexdigest()
                 for n in ("state.json", "wiring.json"))


def _fp(folder: Path) -> tuple:
    return tuple((os.stat(folder / n).st_mtime_ns, os.stat(folder / n).st_size)
                 for n in ("state.json", "wiring.json"))


def _sidecar(env) -> Path:
    return undo_journal.sidecar_path(env["app"].instance_path, _ctx(env)["path"])


def _facts(env) -> dict:
    """Everything a press could change in SM, plus the live files."""
    ctx = _ctx(env)
    store, wc = ctx["store"], ctx["working_copy"]
    sc = _sidecar(env)
    return {
        "log": [(e.dot_path, repr(e.old_value), repr(e.new_value),
                 getattr(e, "actor", None), e.group_id) for e in store.change_log],
        "working": _sha(Path(wc.working_folder)),
        "working_fp": _fp(Path(wc.working_folder)),
        # the saver's own .bak rotation lives here too: a refusal decided
        # before the save writes none of it
        "files": sorted(os.listdir(wc.working_folder)),
        "journal": sc.read_bytes() if sc.exists() else None,
        "dirty": bool(ctx.get("working_dirty")),
        "reapply": json.dumps(ctx.get("pending_reapply"), sort_keys=True, default=repr),
        "reapply_orig": json.dumps(ctx.get("pending_reapply_orig"), sort_keys=True, default=repr),
        "live": _sha(env["live"]),
    }


def _live_doc(env) -> dict:
    return json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))


def _touch(folder: Path, dt: float = 7.0):
    """mtime only -- the same bytes (a backup tool, an editor's no-op save,
    QUAlibrate re-saving unchanged content)."""
    for n in ("state.json", "wiring.json"):
        p = folder / n
        st = os.stat(p)
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + int(dt * 1e9)))


def _edit(env, path="qubits.qA1.f_01", value="5.1e9", headers=None):
    r = env["client"].post("/field/edit", data={"dot_path": path, "value": value},
                           headers=headers or {})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    return r


def _apply(env, *, agent=False, **form):
    headers = {}
    if agent:
        headers.update(AGENT)
        headers.update(JSON)
        form.setdefault("seen_changes", str(len(_ctx(env)["store"].change_log)))
    return env["client"].post("/state/apply-to-live", data=form, headers=headers)


# ----------------------------------------------------------------------
# D-03: the gate asks about content, not about a clock
# ----------------------------------------------------------------------

class TestATouchIsNotAConflict:
    def test_the_windows_apply_lands_after_an_mtime_touch(self, env):
        _edit(env)
        _touch(env["live"])
        r = _apply(env)
        body = r.get_data(as_text=True)
        assert r.status_code == 200, body[:300]
        assert "pending-tray-conflict" not in body, "a touch was refused as stale_live"
        assert _live_doc(env)["qubits"]["qA1"]["f_01"] == 5.1e9
        assert _ctx(env)["store"].change_log == []
        # docs/107: a push that LANDED is journaled (the commit moved to the
        # point where the push is decided -- it must still happen)
        assert len(undo_journal.load(_sidecar(env))) == 1

    def test_an_agents_apply_lands_after_an_mtime_touch(self, env):
        _edit(env, headers=AGENT)
        _touch(env["live"])
        r = _apply(env, agent=True)
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
        assert _live_doc(env)["qubits"]["qA1"]["f_01"] == 5.1e9

    def test_a_resave_of_the_same_content_is_not_a_conflict(self, env):
        """Different bytes (indentation), same content: docs/28's content hash
        is defined over the parsed documents exactly so this hashes equal."""
        _edit(env)
        _write_chip(env["live"], _state(), indent=4)
        r = _apply(env)
        assert "pending-tray-conflict" not in r.get_data(as_text=True)
        assert _live_doc(env)["qubits"]["qA1"]["f_01"] == 5.1e9

    def test_a_touch_between_the_check_and_the_write_is_not_a_conflict(self, env, monkeypatch):
        """The tight re-check right before the write (TOCTOU) compared mtimes
        only: a touch landing there refused an apply whose top check passed."""
        _edit(env)
        wf = Path(_ctx(env)["working_copy"].working_folder)
        real = working_copy.doc_cache.read_pair
        fired = []

        def read_pair(folder, *a, **k):
            if not fired and Path(folder) == wf and k.get("mode") == "hash":
                fired.append(1)            # apply_to_live's payload read
                _touch(env["live"])
            return real(folder, *a, **k)
        monkeypatch.setattr(working_copy.doc_cache, "read_pair", read_pair)
        r = _apply(env)
        assert fired, "the seam was not reached"
        assert "pending-tray-conflict" not in r.get_data(as_text=True)
        assert _live_doc(env)["qubits"]["qA1"]["f_01"] == 5.1e9

    def test_apply_to_live_itself_accepts_a_touch(self, env):
        """The core gate, without the route's preflight in front of it."""
        _edit(env)
        ctx = _ctx(env)
        ctx["saver"].save()
        _touch(env["live"])
        working_copy.apply_to_live(ctx["working_copy"], record=_hub_unrec.unrecorded("refusal unit test: the write is refused before any record"))      # must not raise
        assert _live_doc(env)["qubits"]["qA1"]["f_01"] == 5.1e9

    def test_live_already_holding_the_payload_is_not_a_conflict(self, env):
        """docs/116 at the preflight: live moved -- to exactly what the press
        would write. A no-op is not a conflict."""
        _edit(env)
        _write_chip(env["live"], _state(f01=5.1e9))
        r = _apply(env)
        assert "pending-tray-conflict" not in r.get_data(as_text=True)
        assert _ctx(env)["store"].change_log == []

    def test_revert_last_apply_after_a_touch_points_at_the_pre_apply_values(self, env):
        """The touch is re-anchored where it is first seen, so every later
        look agrees it is not a move -- including the route's backup timing:
        an mtime that still looked moved deferred the pre-apply backup until
        AFTER the write, and "Revert last apply" then held the new values."""
        _edit(env)
        _touch(env["live"])
        _apply(env)
        pre_ts = _ctx(env)["last_apply"]["pre_ts"]
        with env["app"].app_context():
            snap = routes_mod._history().load_snapshot(_ctx(env)["path"], pre_ts)
        assert snap.get_value("qubits.qA1.f_01") == 5.0e9

    def test_the_touch_reanchors_the_sync_point(self, env):
        _touch(env["live"])
        _edit(env)
        _apply(env)
        wc = _ctx(env)["working_copy"]
        assert working_copy.live_changed(wc) is False

    def test_an_approval_lands_after_an_mtime_touch(self, env):
        from quam_state_manager.web import agent_api
        _touch(env["live"])
        ctx = _ctx(env)
        with env["app"].app_context():
            res = agent_api._stage_writes(
                env["app"], str(ctx["path"]),
                [{"path": "qubits.qA1.T1", "new": 3.0e-5, "old": 2.0e-5}],
                "approved:ap-touch", "by_claude", None, True, presser="human:kim")
        assert res["applied"] is True, res
        assert _live_doc(env)["qubits"]["qA1"]["T1"] == 3.0e-5

    def test_a_real_change_is_still_refused_and_never_clobbered(self, env):
        """docs/116, the other half: content that really moved is protected."""
        _edit(env)
        _write_chip(env["live"], _state(t1=9.9e-5))
        r = _apply(env)
        assert "pending-tray-conflict" in r.get_data(as_text=True)
        doc = _live_doc(env)
        assert doc["qubits"]["qA1"]["T1"] == 9.9e-5 and doc["qubits"]["qA1"]["f_01"] == 5.0e9


# ----------------------------------------------------------------------
# A-11 / D-04: a refused press changes nothing, and says so
# ----------------------------------------------------------------------

class TestARefusedApplyChangesNothing:
    def test_the_windows_refused_apply_changes_nothing(self, env):
        _edit(env)
        _write_chip(env["live"], _state(t1=9.9e-5))       # someone else's write
        before = _facts(env)
        r = _apply(env)
        body = r.get_data(as_text=True)
        assert "pending-tray-conflict" in body
        assert _facts(env) == before
        # what SM holds that live does not is on screen: the edit, and the drift
        assert 'data-change-count="1"' in body
        assert _ctx(env).get("live_diverged") is True

    def test_an_agents_refused_apply_changes_nothing_and_says_why(self, env):
        _edit(env, headers=AGENT)
        _write_chip(env["live"], _state(t1=9.9e-5))
        before = _facts(env)
        r = _apply(env, agent=True)
        assert r.status_code == 409
        js = r.get_json()
        assert js["conflict"] == "stale_live"
        assert "still in the tray" in js["message"], js["message"]
        assert _facts(env) == before, "A-11: the tray, the working copy and the journal moved"
        tray = env["client"].get("/api/agent/tray").get_json()
        assert tray["count"] == 1 and tray["live_diverged"] is True, tray

    def test_a_refusal_seen_only_after_the_save_puts_the_save_back(self, env, monkeypatch):
        """A write racing the press: the top check passes, the door saves, and
        only then does live move. The save is rolled back."""
        _edit(env)
        before = _facts(env)
        saver = _ctx(env)["saver"]
        real = saver.save

        def save_then_race(*a, **k):
            out = real(*a, **k)
            _write_chip(env["live"], _state(t1=9.9e-5))
            return out
        monkeypatch.setattr(saver, "save", save_then_race)
        r = _apply(env)
        assert "pending-tray-conflict" in r.get_data(as_text=True)
        after = _facts(env)
        assert after.pop("live") != before.pop("live"), "the racing write stands"
        after.pop("files"), before.pop("files")      # the save's .bak rotation
        assert after == before
        assert _live_doc(env)["qubits"]["qA1"]["T1"] == 9.9e-5

    def test_a_change_the_mtimes_hide_is_refused_and_put_back(self, env):
        """Coarse mtime granularity: live content changed, the mtimes did not.
        The route's preflight is stat-first (it reads nothing then); the door's
        own content check refuses, and the save is put back."""
        _edit(env)
        before = _facts(env)
        fp = _fp(env["live"])
        _write_chip(env["live"], _state(t1=9.9e-5))
        for (mt, _), n in zip(fp, ("state.json", "wiring.json")):
            os.utime(env["live"] / n, ns=(mt, mt))
        assert _fp(env["live"])[0][0] == fp[0][0]
        r = _apply(env)
        assert "pending-tray-conflict" in r.get_data(as_text=True)
        after = _facts(env)
        assert after.pop("live") != before.pop("live")
        after.pop("files"), before.pop("files")      # the save's .bak rotation
        assert after == before
        assert _live_doc(env)["qubits"]["qA1"]["T1"] == 9.9e-5

    def test_an_edit_typed_during_the_press_stays_after_the_restored_ones(self, env, monkeypatch):
        _edit(env)
        saver = _ctx(env)["saver"]
        real = saver.save

        def save_edit_then_race(*a, **k):
            out = real(*a, **k)
            _edit(env, path="qubits.qA1.T1", value="3e-5")     # another keystroke
            _write_chip(env["live"], _state(t1=9.9e-5))
            return out
        monkeypatch.setattr(saver, "save", save_edit_then_race)
        _apply(env)
        ctx = _ctx(env)
        assert [e.dot_path for e in ctx["store"].change_log] == ["qubits.qA1.f_01", "qubits.qA1.T1"]
        wc = ctx["working_copy"]
        assert working_copy.working_content_hash(wc) == wc.synced_live_hash

    def test_a_refusal_never_writes_over_a_working_copy_it_did_not_write(self, env, monkeypatch):
        """Whatever replaced the working pair after this press's save (with no
        store mutation to show for it) is not the press's to undo: the stat
        fingerprint of the pair must still be the one the save produced."""
        _edit(env)
        ctx = _ctx(env)
        wf = Path(ctx["working_copy"].working_folder)
        real = working_copy.apply_to_live

        def rewrite_then_race(wc, *a, **k):        # after the press's save
            doc = json.loads((wf / "state.json").read_text(encoding="utf-8"))
            doc["qubits"]["qA1"]["chi"] = -7
            (wf / "state.json").write_text(json.dumps(doc), encoding="utf-8")
            _write_chip(env["live"], _state(t1=9.9e-5))
            return real(wc, *a, **k)
        monkeypatch.setattr(working_copy, "apply_to_live", rewrite_then_race)
        _apply(env)
        w = json.loads((wf / "state.json").read_text(encoding="utf-8"))["qubits"]["qA1"]
        assert w.get("chi") == -7, "the press put its old bytes back over a write it did not make"

    def test_a_refusal_never_puts_back_over_another_windows_save(self, env, monkeypatch):
        """Between this press's save and its refusal another window SAVED: the
        working copy is no longer what this press wrote. Restoring the old
        bytes would drop that save; the press's save stands instead (journaled,
        as before docs/255)."""
        _edit(env)
        ctx = _ctx(env)
        saver = ctx["saver"]
        real = saver.save

        def save_then_other_window(*a, **k):
            out = real(*a, **k)
            _edit(env, path="qubits.qA1.T1", value="3e-5")
            real()                                             # the other window's save
            _write_chip(env["live"], _state(t1=9.9e-5))
            return out
        monkeypatch.setattr(saver, "save", save_then_other_window)
        _apply(env)
        w = json.loads((Path(ctx["working_copy"].working_folder) / "state.json")
                       .read_text(encoding="utf-8"))["qubits"]["qA1"]
        assert w["T1"] == 3e-5 and w["f_01"] == 5.1e9, "a save was put back over"
        assert _sidecar(env).exists(), "the save that stands is journaled"

    def test_a_keep_mine_whose_confirm_went_stale_changes_nothing(self, env):
        """QA correctness-r2-09's re-ask: the Keep-mine confirm named a live
        content that is no longer there. It used to be checked AFTER the save."""
        _edit(env)
        _write_chip(env["live"], _state(t1=9.9e-5))
        before = _facts(env)
        r = env["client"].post("/state/apply-to-live",
                               data={"force": "1", "expect_live_hash": "0" * 16},
                               headers=JSON)
        assert r.status_code == 409 and r.get_json()["conflict"] == "live_moved"
        assert _facts(env) == before

    def test_a_forced_push_with_no_backup_changes_nothing(self, env, monkeypatch):
        """QA correctness-r2-01 refuses a forced push it cannot back up -- after
        the save, so the save is put back."""
        _edit(env)
        _write_chip(env["live"], _state(t1=9.9e-5))
        before = _facts(env)

        class _NoHistory:
            def check_and_snapshot(self, *a, **k):
                raise OSError("simulated: the live pair cannot be read")

            def snapshot_ts_for_current_content(self, *a, **k):
                return None
        monkeypatch.setattr(routes_mod, "_history", lambda: _NoHistory())
        r = env["client"].post("/state/apply-to-live", data={"force": "1"}, headers=JSON)
        assert r.status_code == 409 and r.get_json()["conflict"] == "no_backup"
        after = _facts(env)
        after.pop("files"), before.pop("files")      # the save's .bak rotation
        assert after == before

    def test_an_auto_apply_refusal_keeps_the_edit_in_the_tray(self, env):
        c = env["client"]
        c.post("/auto-apply/arm")
        _edit(env)
        _write_chip(env["live"], _state(t1=9.9e-5))
        before = _facts(env)
        r = _apply(env)
        assert "autoApplyDisarm" in r.headers.get("HX-Trigger", "")
        assert _facts(env) == before


class TestAFailedWriteIsNotARefusal:
    def test_the_save_stands_journaled_when_the_live_write_fails(self, env, monkeypatch):
        """A write that FAILED (a reader holding state.json on Windows, a
        read-only share) is not the chip refusing: the edits stay saved in the
        working copy as before, and that save is journaled (docs/107)."""
        from quam_state_manager.core import safe_io
        _edit(env)
        live = str(env["live"])
        real = safe_io.write_state_wiring_bytes

        def locked(folder, *a, **k):
            if Path(folder).resolve() == Path(live).resolve():
                raise PermissionError(13, "simulated: the live folder is read-only")
            return real(folder, *a, **k)
        monkeypatch.setattr(safe_io, "write_state_wiring_bytes", locked)
        r = _apply(env)
        assert r.status_code == 500
        ctx = _ctx(env)
        assert ctx["store"].change_log == [] and ctx.get("working_dirty")
        assert len(undo_journal.load(_sidecar(env))) == 1
        assert _live_doc(env)["qubits"]["qA1"]["f_01"] == 5.0e9


class TestARefusedEditIsStillResolvable:
    def _refused(self, env, **kw):
        _edit(env, **kw)
        _write_chip(env["live"], _state(t1=9.9e-5))
        r = _apply(env, agent="headers" in kw)
        assert r.status_code in (200, 409)
        return r

    def test_pull_and_apply_lands_both_values(self, env):
        self._refused(env)
        r = env["client"].post("/state/sync", data={"mode": "apply"})
        assert r.get_json()["status"] == "ok", r.get_json()
        doc = _live_doc(env)
        assert doc["qubits"]["qA1"]["f_01"] == 5.1e9 and doc["qubits"]["qA1"]["T1"] == 9.9e-5
        units = undo_journal.load(_sidecar(env))
        assert [u["entries"][0]["path"] for u in units] == ["qubits.qA1.f_01"], units

    def test_keep_mine_lands_the_edit(self, env):
        self._refused(env)
        r = env["client"].post("/state/apply-to-live", data={"force": "1"})
        assert r.status_code == 200
        assert _live_doc(env)["qubits"]["qA1"]["f_01"] == 5.1e9

    def test_take_live_discards_the_edit(self, env):
        self._refused(env)
        r = env["client"].post("/state/sync", data={"mode": "discard", "seen_changes": "1"})
        assert r.get_json()["status"] == "ok", r.get_json()
        ctx = _ctx(env)
        assert ctx["store"].change_log == [] and ctx["store"].get_value("qubits.qA1.T1") == 9.9e-5

    def test_the_agent_can_undo_its_own_refused_row(self, env):
        """D-04's way out: the bridge says "undo, take_live, re-stage" -- with
        the row saved away, the agent's undo hit an empty tray and was refused
        `journal`."""
        self._refused(env, headers=AGENT)
        r = env["client"].post("/undo", headers={**AGENT, **JSON})
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
        assert _ctx(env)["store"].change_log == []
        assert _ctx(env)["store"].get_value("qubits.qA1.f_01") == 5.0e9


class TestTheOtherDoor:
    def test_a_staged_push_refused_after_its_save_changes_nothing(self, env):
        """`_sync_pull_apply_to_live` (Pull & apply, the docs/65 staged carve-out,
        Dataset Apply to chip, the Ctrl+Z walk) saves before it writes, too."""
        _edit(env)
        assert env["client"].post("/save").status_code in (200, 204)
        _ctx(env)["staged_base"] = True                 # a staged version + edits
        _edit(env, path="qubits.qA1.T1", value="3e-5")
        _write_chip(env["live"], _state(t1=9.9e-5))
        before = _facts(env)
        r = env["client"].post("/state/sync", data={"mode": "apply"})
        assert r.get_json()["status"] == "conflict", r.get_json()
        after = _facts(env)
        after.pop("files"), before.pop("files")      # the save's .bak rotation
        assert after == before


# ----------------------------------------------------------------------
# P3: a refused approval leaves no type drift behind
# ----------------------------------------------------------------------

class TestARefusedApprovalLeavesNoDrift:
    def test_an_int_stays_an_int_and_the_working_copy_is_untouched(self, env, monkeypatch):
        """The approval's value is a non-integral float (a fit result) on an
        int field, so staging it widens the field to float. The door refused
        after its save, and the old take-back re-wrote the old value through
        that widened type: 7126044234 came back as 7126044234.0, the working
        copy no longer matched the sync point with an empty tray, and the next
        apply carried the float to the chip."""
        from quam_state_manager.web import agent_api
        ctx = _ctx(env)
        before = _facts(env)
        saver = ctx["saver"]
        real = saver.save

        def save_then_race(*a, **k):
            out = real(*a, **k)
            _write_chip(env["live"], _state(t1=9.9e-5))
            return out
        monkeypatch.setattr(saver, "save", save_then_race)
        with env["app"].app_context():
            res = agent_api._stage_writes(
                env["app"], str(ctx["path"]),
                [{"path": "qubits.qA1.resonator.RF_frequency", "new": 7126300123.4,
                  "old": 7126044234}],
                "approved:ap-drift", "by_claude", None, True, presser="human:kim")
        assert res["applied"] is False, res
        v = ctx["store"].get_value("qubits.qA1.resonator.RF_frequency")
        assert v == 7126044234 and type(v) is int, repr(v)
        after = _facts(env)
        for k in ("live", "files"):
            after.pop(k), before.pop(k)
        assert after == before
        wc = ctx["working_copy"]
        assert working_copy.working_content_hash(wc) == wc.synced_live_hash,             "SM holds content that is neither live nor in the tray"
