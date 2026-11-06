# -*- coding: utf-8 -*-
"""docs/187 — a push that finds the chip moved must MERGE, not turn itself off.

Customer, on-site (2026-09-12): "live edit에서 enter를 누르면 그 다음부터
auto sync가 깨짐", and "이유없이 pull&apply 문구가 뜬다".

Their bench is the reason: SM (pid 4928, :5050) has
``D:\\work\\Customer_Codes\\quam_states\\lab-F`` open, and that is
qualibrate's own ``state_path`` — its log saves the machine there on every node
run, every 30–60 s. So a node writes the chip between two of the user's edits
as a matter of course, the push conflicts, and the session disarmed. Auto-Sync
died within a minute of arming, and every later edit silently stopped reaching
the chip while the user kept typing.

The rule these pin: a session that armed PULL has already granted SM permission
to take live changes, so the merge is the composition of two permissions the
user gave — not a new one. Push-only sessions keep docs/117's disarm exactly.
"""
import json
import time
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app
import quam_state_manager.web.routes as routes_mod

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}


def _state(f01=5.0e9, t1=2.0e-5):
    return {
        "qubits": {"qA1": {"id": "qA1", "f_01": f01, "T1": t1}},
        "qubit_pairs": {},
        "active_qubit_names": ["qA1"],
    }


def _write_chip(folder: Path, state: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state())
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    c.environ_base["HTTP_X_SM_WINDOW_ID"] = "first"
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live, "tmp": tmp_path}


def _ctx(env):
    with env["app"].app_context():
        return routes_mod._active_ctx()


def _live_f01(env) -> float:
    doc = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
    return doc["qubits"]["qA1"]["f_01"]


def _edit(env, path="qubits.qA1.f_01", value="5.1e9"):
    return env["client"].post("/field/edit", data={"dot_path": path, "value": value})


def _arm(env, *, pull=True, push=True, replace=False):
    """Arm through the REAL door, the way the popup does."""
    return env["client"].post("/auto-sync/set", data={
        "pull": "1" if pull else "0",
        "pull_replace": "1" if replace else "0",
        "push": "1" if push else "0",
    })


def _sess(env):
    return _ctx(env).get("auto_apply")


class TestTheMergeReplacesTheDisarm:
    """The reported bug, end to end through the real routes."""

    def test_a_node_writing_between_two_edits_does_not_kill_the_session(self, env):
        c = env["client"]
        assert _arm(env, pull=True, push=True).status_code == 200
        _edit(env)                                   # the user types + Enter
        _write_chip(env["live"], _state(f01=7.7e9))  # a node saves the chip

        r = c.post("/state/apply-to-live")

        assert _sess(env) is not None, (
            "Auto-Sync turned itself off on exactly the event the user armed "
            "pull to handle")
        assert "autoApplyDisarm" not in r.headers.get("HX-Trigger", "")
        assert "autoSyncMerge" in r.headers.get("HX-Trigger", ""), (
            "nothing told the client it may resolve this")
        # nothing was written by the refused push — the node's value stands
        assert _live_f01(env) == 7.7e9

    def test_the_edit_is_still_there_to_be_merged(self, env):
        c = env["client"]
        _arm(env, pull=True, push=True)
        _edit(env)
        _write_chip(env["live"], _state(f01=7.7e9))
        c.post("/state/apply-to-live")
        ctx = _ctx(env)
        # docs/255: it survives IN THE TRAY (a refused push changes nothing),
        # which is exactly what the merge's pull-and-replay takes from
        assert [e.dot_path for e in ctx["store"].change_log], (
            "the user's edit must survive the conflict to be re-applied")

    def test_and_the_merge_lands_both_values(self, env):
        """The whole point: the node's write AND the user's edit, not a choice
        between them. This is the door the client is told to press."""
        c = env["client"]
        _arm(env, pull=True, push=True)
        _edit(env, path="qubits.qA1.T1", value="1.25e-5")
        _write_chip(env["live"], _state(f01=7.7e9))
        c.post("/state/apply-to-live")                  # conflict -> merge signal

        r = c.post("/state/sync", data={"mode": "apply"})
        assert r.status_code == 200, r.data[:400]
        doc = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
        assert doc["qubits"]["qA1"]["f_01"] == 7.7e9, "the node's write was lost"
        assert doc["qubits"]["qA1"]["T1"] == 1.25e-5, "the user's edit was lost"


class TestWhatDidNotChange:
    """docs/117's disarm is still the rule everywhere it was the rule."""

    def test_a_push_only_session_still_disarms(self, env):
        c = env["client"]
        _arm(env, pull=False, push=True)
        _edit(env)
        _write_chip(env["live"], _state(f01=7.7e9))
        r = c.post("/state/apply-to-live")
        assert "autoApplyDisarm" in r.headers.get("HX-Trigger", "")
        assert _sess(env) is None, (
            "without pull permission SM must not take the live change")
        assert "autoSyncMerge" not in r.headers.get("HX-Trigger", "")

    def test_the_legacy_arm_route_is_push_only_and_unchanged(self, env):
        """`/auto-apply/arm` is the pre-docs/120 door; it grants push alone, so
        it must keep the old behaviour byte for byte."""
        c = env["client"]
        c.post("/auto-apply/arm")
        assert (_sess(env) or {}).get("pull") in (False, None)
        _edit(env)
        _write_chip(env["live"], _state(f01=7.7e9))
        r = c.post("/state/apply-to-live")
        assert "autoApplyDisarm" in r.headers.get("HX-Trigger", "")
        assert _sess(env) is None

    def test_an_unarmed_apply_is_untouched(self, env):
        c = env["client"]
        _edit(env)
        _write_chip(env["live"], _state(f01=7.7e9))
        r = c.post("/state/apply-to-live")
        assert "pending-tray-conflict" in r.data.decode()
        assert r.headers.get("HX-Trigger") is None or \
            "autoSyncMerge" not in r.headers.get("HX-Trigger", "")
        assert _live_f01(env) == 7.7e9


class TestTheBudget:
    """A node between two edits is ordinary. A merge that keeps conflicting is
    not, and a background writer must never retry for ever."""

    def test_repeated_live_movement_never_disarms(self, env):
        c = env["client"]
        _arm(env)
        for i in range(12):
            _edit(env, value=f"5.{i}e9")
            _write_chip(env["live"], _state(f01=7.0e9 + i))
            r = c.post("/state/apply-to-live")
            assert _sess(env) is not None
            event = json.loads(r.headers["HX-Trigger"])["autoSyncMerge"]
            assert event["class"] == "transient"
            assert event["delay"] == (2, 5, 15, 30, 60)[min(i, 4)]

    def test_an_apply_that_lands_refills_the_budget(self, env):
        c = env["client"]
        _arm(env)
        _edit(env)
        _write_chip(env["live"], _state(t1=9.9e-5))
        c.post("/state/apply-to-live")
        assert _sess(env)["merge_tries"] == 1
        assert c.post("/state/sync", data={"mode": "apply"}).get_json()["status"] == "ok"
        _edit(env, value="5.5e9")
        c.post("/state/apply-to-live")
        assert _sess(env)["merge_tries"] == 0

    def test_the_MERGE_itself_refills_the_budget(self, env):
        c = env["client"]
        _arm(env)
        for i in range(6):
            before = _live_f01(env)
            value = float(f"6.{i}e9")
            _edit(env, value=str(value))
            _write_chip(env["live"], _state(f01=before, t1=(i + 3) * 1e-5))
            r = c.post("/state/apply-to-live")
            assert "autoSyncMerge" in r.headers.get("HX-Trigger", "")
            m = c.post("/state/sync", data={"mode": "apply"})
            assert m.get_json()["status"] == "ok"
            assert _live_f01(env) == value
            assert _sess(env)["merge_tries"] == 0
            assert not _sess(env).get("merge_retry")



class TestTheClientPressesTheDoorItIsGiven:
    """docs/187 review R8. These were four greps over fixed character windows
    of auto-apply.js. The review's verdict was blunt and right -- "the handler
    that never runs they claim to guard cannot fail" -- and
    `test_the_wait_is_bounded` passed with an UNBOUNDED wait. Adding a single
    comment to the source broke two of them.

    So the behaviour is EXECUTED now, against the shipped file, and what is
    left here as text are only cross-file facts a character window cannot
    express.
    """

    def test_the_shipped_client_behaves(self):
        """tests/autosync_merge_selfcheck.cjs: dispatches the real events at
        the real file under jsdom and watches what it does -- that a free latch
        presses at once, that a held latch DEFERS and then presses, that an
        unclearable latch never presses AND the attempt is abandoned rather
        than firing seconds late, that the chip token comes from the signal,
        and every branch of the replace warning."""
        root = Path(__file__).resolve().parents[1]
        drv = root / "tests" / "autosync_merge_selfcheck.cjs"
        assert drv.exists(), drv
        if shutil.which("node") is None:
            pytest.skip("node not installed")
        if subprocess.run(["node", "-e", "require.resolve('jsdom')"],
                          capture_output=True, cwd=str(root)).returncode:
            pytest.skip("jsdom not installed (npm install)")
        r = subprocess.run(["node", str(drv)], capture_output=True, text=True,
                           errors="replace", timeout=300, cwd=str(root))
        assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
        assert "assertions" in (r.stdout or ""), r.stdout

    def test_the_event_names_match_on_both_sides(self):
        """The name is a CONTRACT between two files. Renaming it on one side
        alone would kill the feature with every pin green -- which is what the
        review demonstrated by doing exactly that (`autoSyncPulledV2`)."""
        root = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"
        py = (root / "routes.py").read_text(encoding="utf-8")
        js = (root / "static" / "auto-apply.js").read_text(encoding="utf-8")
        for name in ("autoSyncMerge", "autoSyncPulled", "autoSyncRestored", "autoSyncRetry"):
            assert '"%s"' % name in py, (
                "the server no longer emits %s" % name)
            assert "'%s'" % name in js, (
                "the client no longer listens for %s" % name)
        # …and no near-miss spelling on either side, which is how a rename
        # half-lands.
        import re
        emitted = set(re.findall(r'"(autoSync[A-Za-z]+)"', py))
        heard = set(re.findall(r"addEventListener\('(autoSync[A-Za-z]+)'", js))
        assert emitted == heard, (
            "the server emits %s and the client hears %s" % (sorted(emitted), sorted(heard)))

    def test_the_door_it_presses_is_the_one_the_tray_offers(self):
        """A cross-file fact: the automatic merge must press the SAME thing the
        conflict tray's primary button offers a human."""
        root = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"
        # sync-ux 2026-09-25 (user decision: one control + one panel): the conflict tray's buttons moved into the
        # panel and onto the control -- the SAME door must still be what a
        # human is offered there: the control's attached ⇄ Pull & apply and the
        # panel's merge (SyncPanel.merge -> doStateSync("apply", ...)).
        ctl = (root / "templates" / "_sync_control.html").read_text(encoding="utf-8")
        app = (root / "static" / "app.js").read_text(encoding="utf-8")
        js = (root / "static" / "auto-apply.js").read_text(encoding="utf-8")
        assert "doStateSync('apply')" in ctl, (
            "the control no longer offers the merge the automatic path presses")
        m = app.index("function merge(btn)")
        # the window is the function body, not a character budget (R-f added
        # the keep-applied flag and its comment above the press)
        assert 'window.doStateSync("apply"' in app[m:app.index("function reapply()", m)]
        assert "window.doStateSync(d.mode || 'apply'" in js
        assert "d.mode = 'apply'" in js[js.index("addEventListener('autoSyncMerge'"):]


class TestItIsOneSessionFlag:
    def test_a_fresh_session_starts_with_a_full_budget(self, env):
        _arm(env, pull=True, push=True)
        assert _sess(env)["merge_tries"] == 0

    def test_re_arming_clears_a_spent_budget(self, env):
        c = env["client"]
        _arm(env, pull=True, push=True)
        _edit(env)
        _write_chip(env["live"], _state(f01=7.7e9))
        c.post("/state/apply-to-live")
        assert _sess(env)["merge_tries"] == 1
        _arm(env, pull=True, push=True)
        assert _sess(env)["merge_tries"] == 0


class TestAReplacePullSaysSo:
    """docs/187 ② — `replace` discards the user's unapplied edits, and NOTHING
    in the tree listened to the event that announced it. Measured:

        replace=False  pull->204  pending 1 -> 1  (it asks)
        replace=True   pull->200  pending 2 -> 0  (discarded, silently)

    The semantics are the checkbox's and are unchanged; what is pinned here is
    that it is SAID, and that the recovery named really exists.
    """

    def _diverge_and_pull(self, env, *, replace):
        c = env["client"]
        _arm(env, pull=True, push=True, replace=replace)
        _edit(env, path="qubits.qA1.T1", value="9.99e-5")
        _write_chip(env["live"], _state(f01=7.7e9))
        with env["app"].test_request_context():
            ctx = routes_mod._active_ctx()
            ctx["_live_hash_checked_at"] = None
            routes_mod._refresh_live_diverged(ctx)
        return c.post("/auto-sync/pull", data={"dom_dirty": "0"})

    def test_without_replace_it_refuses_and_keeps_the_edit(self, env):
        r = self._diverge_and_pull(env, replace=False)
        assert r.status_code == 204
        assert len(_ctx(env)["store"].change_log or []) == 1, "the edit was dropped"

    def test_with_replace_it_discards_and_reports_how_many(self, env):
        r = self._diverge_and_pull(env, replace=True)
        assert r.status_code == 200
        assert len(_ctx(env)["store"].change_log or []) == 0, "nothing was replaced"
        trig = r.headers.get("HX-Trigger", "")
        assert "autoSyncPulled" in trig
        payload = json.loads(trig)["autoSyncPulled"]
        assert payload["replaced"] is True
        assert payload["count"] >= 1, (
            "it reported a replace of zero edits — the count is taken after the "
            "pull, when there is nothing left to count")

    def test_a_pull_that_discards_nothing_does_not_claim_it_did(self, env):
        """No edits pending: the pull is silent, and must not raise a warning
        about work nobody lost."""
        c = env["client"]
        _arm(env, pull=True, push=True, replace=True)
        _write_chip(env["live"], _state(f01=7.7e9))
        with env["app"].test_request_context():
            ctx = routes_mod._active_ctx()
            ctx["_live_hash_checked_at"] = None
            routes_mod._refresh_live_diverged(ctx)
        r = c.post("/auto-sync/pull", data={"dom_dirty": "0"})
        if r.status_code == 200 and "autoSyncPulled" in r.headers.get("HX-Trigger", ""):
            p = json.loads(r.headers["HX-Trigger"])["autoSyncPulled"]
            assert p["replaced"] is False and p["count"] == 0

    def test_the_pull_still_snapshots_before_discarding(self, env):
        """The backup is real and worth having -- it is how you get back the
        state the chip was in before this pull."""
        before = _snap_count(env)
        self._diverge_and_pull(env, replace=True)
        assert _snap_count(env) > before, "a discarding pull took no backup"

    def test_the_message_does_not_promise_a_recovery_that_does_not_exist(self, env):
        """docs/187 2, corrected. The first version of the toast said "State
        History can bring it back". It cannot: `check_and_snapshot` reads the
        LIVE folder's files, and a pending edit lives in the in-memory change
        log, so no snapshot ever holds it.

        This pin is the measurement, not the wording: it asserts that NO
        snapshot on disk contains the discarded value, and that the shipped
        message therefore does not offer State History for it.
        """
        c = env["client"]
        THE_EDIT = 9.99e-5
        _arm(env, pull=True, push=True, replace=True)
        _edit(env, path="qubits.qA1.T1", value=str(THE_EDIT))
        _write_chip(env["live"], _state(f01=7.7e9))
        with env["app"].test_request_context():
            ctx = routes_mod._active_ctx()
            ctx["_live_hash_checked_at"] = None
            routes_mod._refresh_live_diverged(ctx)
        assert c.post("/auto-sync/pull", data={"dom_dirty": "0"}).status_code == 200

        hist = Path(env["app"].instance_path) / "history"
        held = []
        for f in hist.rglob("state.json"):
            try:
                doc = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if doc.get("qubits", {}).get("qA1", {}).get("T1") == THE_EDIT:
                held.append(str(f))
        # If this ever starts failing because a snapshot DOES hold it, the
        # product got better and the message should be revisited -- read the
        # message, do not just flip the assert.
        assert not held, (
            "a snapshot holds the discarded edit after all — the toast may now "
            "honestly offer State History: " + str(held[:2]))

        js = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"
              / "static" / "auto-apply.js").read_text(encoding="utf-8")
        i = js.index("document.addEventListener('autoSyncPulled'")
        blk = js[i:i + 1400]
        assert "State History can bring it back" not in blk, (
            "the toast promises a recovery that was measured not to exist")
        assert "not recoverable" in blk, "it does not say what actually happened"
        # R7: a number must cover everything it describes, or not be given
        assert "other unapplied work" in blk, (
            "a count beside other lost work is presented as the whole loss")
        assert "Untick" in blk, "it does not name the setting that prevents this"


    def test_typed_but_uncommitted_grid_cells_also_block_the_pull(self, env):
        """The mutation sweep's finding: the outer guard looked like a
        duplicate of the one inside the build lock, and for a SERVER-side edit
        it is. It is not for `dom_dirty`.

        A fill-down or a pasted column lives only in the DOM until Apply, so
        `change_log` and `working_dirty` are both clean and the inner guard —
        which asks only `_quam_ctx_dirty` — would let the pull through and wipe
        a whole filled column with no prompt. Only the client can see that
        work, which is why it reports it.
        """
        c = env["client"]
        _arm(env, pull=True, push=True, replace=False)
        _write_chip(env["live"], _state(f01=7.7e9))
        with env["app"].test_request_context():
            ctx = routes_mod._active_ctx()
            ctx["_live_hash_checked_at"] = None
            routes_mod._refresh_live_diverged(ctx)
        assert not routes_mod._quam_ctx_dirty(_ctx(env)), (
            "the fixture must be server-CLEAN or this pin proves nothing")

        r = c.post("/auto-sync/pull", data={"dom_dirty": "1"})
        assert r.status_code == 204, (
            "the pull ran over grid cells only the browser can see")

    def test_somebody_listens(self):
        js = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"
              / "static" / "auto-apply.js").read_text(encoding="utf-8")
        assert "document.addEventListener('autoSyncPulled'" in js, (
            "the server announces the replace and nothing in the tree hears it")
        i = js.index("document.addEventListener('autoSyncPulled'")
        blk = js[i:i + 900]
        assert "d.replaced" in blk, "it warns even when nothing was replaced"


def _snap_count(env) -> int:
    hist = Path(env["app"].instance_path) / "history"
    if not hist.exists():
        return 0
    return sum(1 for p in hist.rglob("*") if p.is_dir() and (p / "state.json").exists())


class TestTheConflictTrayShowsTheSession:
    """docs/187 (3). Every pre-fix snapshot of the reproduction had BOTH
    `pillOn=false` and `pillOff=false`: the conflict tray replaces
    `#pending-tray`, where the pill lives, so exactly while a live-write
    conflict was on screen nothing said whether Auto-Sync was on, nothing said
    it had just been turned off, and there was no control to turn it back on.
    """

    def _conflict(self, env, *, pull):
        c = env["client"]
        _arm(env, pull=pull, push=True)
        _edit(env)
        _write_chip(env["live"], _state(f01=7.7e9))
        return c.post("/state/apply-to-live").data.decode()

    def test_the_tray_carries_the_pill_at_all(self, env):
        html = self._conflict(env, pull=True)
        assert "pending-tray-conflict" in html, "not the conflict tray"
        assert "auto-apply-pill" in html, (
            "the conflict tray renders no Auto-Sync pill, so the session is "
            "invisible exactly when it matters")

    def test_a_surviving_session_shows_as_ON(self, env):
        html = self._conflict(env, pull=True)
        assert "auto-apply-on" in html
        assert _sess(env) is not None

    def test_a_disarmed_session_shows_as_OFF_and_can_be_re_armed(self, env):
        html = self._conflict(env, pull=False)      # push-only -> disarms
        assert _sess(env) is None
        assert "auto-apply-on" not in html, (
            "the pill painted itself ON while the session was being turned off")
        assert "auto-apply-pill" in html, (
            "after turning itself off the tray showed no pill at all")
        # OFF (re-armable) or BLOCKED (with the reason) are both honest; ON is
        # not, and neither is nothing.
        assert ("auto-apply-off" in html) or ("auto-apply-blocked" in html), html[:400]
        if "auto-apply-off" in html:
            assert "AutoSync.toggle" in html, "the pill is not clickable"

    def test_it_SAYS_it_was_turned_off(self, env):
        """A toast is gone in seconds; this tray is what the user reads while
        deciding what to do."""
        html = self._conflict(env, pull=False)
        assert "turned" in html and "off" in html
        assert "tray-conflict-autosync" in html

    def test_it_does_not_claim_a_disarm_that_did_not_happen(self, env):
        html = self._conflict(env, pull=True)       # merges, stays armed
        assert "Auto-Sync is off" not in html, (
            "the tray told the user Auto-Sync was off while it was still on")
        assert "still on" in html

    def test_an_unarmed_conflict_says_nothing_about_auto_sync(self, env):
        c = env["client"]
        _edit(env)
        _write_chip(env["live"], _state(f01=7.7e9))
        html = c.post("/state/apply-to-live").data.decode()
        assert "pending-tray-conflict" in html
        assert "tray-conflict-autosync" not in html, (
            "a user who never armed Auto-Sync was told about it")

    def test_the_sync_route_conflict_carries_it_too(self, env, monkeypatch):
        """Both conflict doors go through one renderer, so neither can be the
        one that forgets a variable.

        The first version of this pin was conditional -- `if the response was
        a conflict, assert` -- and the fixture never produced one, so it
        asserted nothing and the sweep found it GREEN. The write is forced to
        fail instead, which is the only thing this pin is about: what that
        route RENDERS when it does.
        """
        from quam_state_manager.core import working_copy as _wc

        c = env["client"]
        _arm(env, pull=True, push=True)
        _edit(env)

        def _always_stale(*a, **k):
            raise _wc.StaleLiveError("forced for the render")
        monkeypatch.setattr(_wc, "apply_to_live", _always_stale)

        r = c.post("/state/sync", data={"mode": "apply"})
        body = r.get_json() or {}
        assert body.get("status") == "conflict", body
        html = body.get("tray_html") or ""
        assert "pending-tray-conflict" in html
        assert "auto-apply-pill" in html, (
            "the sync route's conflict tray renders no Auto-Sync pill")


class TestOnePillRenderer:
    """The pill is one partial. A second copy would drift from the first."""

    def _t(self, name):
        return (Path(__file__).resolve().parents[1] / "quam_state_manager"
                / "web" / "templates" / name).read_text(encoding="utf-8")

    def test_both_trays_include_the_same_partial(self):
        for f in ("_pending_tray.html", "_state_apply_conflict.html"):
            assert "_auto_sync_pill.html" in self._t(f), f

    def test_neither_tray_still_spells_the_pill_out(self):
        for f in ("_pending_tray.html", "_state_apply_conflict.html"):
            assert "auto-apply-pill auto-apply-on" not in self._t(f), (
                f + " carries its own copy of the pill")

    def test_the_partial_imports_the_macro_it_uses(self):
        """A macro imported by the INCLUDING template is not visible inside an
        include — the second caller failed with 'icon_bolt is undefined' the
        moment it existed."""
        t = self._t("_auto_sync_pill.html")
        assert "import icon_bolt" in t and "icon_bolt(" in t


class TestTheMergeDoorIsGated:
    """docs/187 review R3/R5. The conflict tray IS `#pending-tray` while it is
    on screen, and it published NONE of the tray's data-* contract. Measured in
    a real browser run and not acted on at the time:

        [after Enter]  auto=null  count=null  wdirty=null  conflict=true

    `doStateSync` reads `_seenSig` from `data-change-sig`, so the AUTOMATIC
    merge declared no change set and the docs/179 unseen-edit gate could not
    fire on the one door that now presses itself.
    """

    def _conflict_html(self, env):
        c = env["client"]
        _arm(env, pull=True, push=True)
        _edit(env)
        _write_chip(env["live"], _state(f01=7.7e9))
        return c.post("/state/apply-to-live").data.decode()

    def test_the_conflict_tray_publishes_the_change_signature(self, env):
        html = self._conflict_html(env)
        assert 'data-change-sig="' in html, (
            "the merge presses a live-write door declaring no change set")
        sig = html.split('data-change-sig="', 1)[1].split('"', 1)[0]
        assert sig, "an empty signature reads as 'no opinion' — the gate is off"
        with env["app"].app_context():
            assert sig == routes_mod._change_log_sig(_ctx(env)["store"])

    def test_it_publishes_the_count_and_the_seq_too(self, env):
        html = self._conflict_html(env)
        assert 'data-change-count="' in html
        assert 'data-working-dirty="' in html
        assert 'data-seq="' in html, "PaneState has nothing to compare (docs/110)"

    def test_it_deliberately_does_NOT_publish_the_armed_flag(self, env):
        """Not an oversight. While this tray is up the merge (or the human) is
        driving; a flusher that read itself as armed here would re-press the
        door that just refused, conflict again, and burn a budget try against
        its own merge. The pill still shows the truth because it renders from
        `auto_sync`."""
        html = self._conflict_html(env)
        i = html.index('class="pending-tray pending-tray-conflict"')
        root = html[i:html.index(">", i)]
        assert "data-auto-apply" not in root, (
            "the flusher will re-press the door that just refused")
        assert "auto-apply-on" in html, (
            "…but the pill must still say the session is on")

    def test_the_gate_actually_refuses_a_stale_merge(self, env):
        """End to end: the sig the tray published is what the gate checks, so a
        press declaring a DIFFERENT set is refused on the merge door."""
        self._conflict_html(env)
        r = env["client"].post("/state/sync", data={
            "mode": "apply", "seen_changes": "1",
            "seen_sig": "notthesignature"})
        assert r.status_code == 409, r.status_code
        assert (r.get_json() or {}).get("status") == "unseen_changes", r.get_json()


class TestTheMergeIsPinnedToItsChip:
    """docs/187 review R2. /state/sync reads _active_ctx() and never consulted
    expect_chip -- tolerable while a HUMAN pressed "Pull & apply" with that
    chip on screen. docs/187 made it a door the server asks the client to press
    by itself, up to ~2s later, and the context registry is shared with every
    other window. So the signal names the chip and the door holds the caller
    to it."""

    def _signal(self, env):
        c = env["client"]
        _arm(env, pull=True, push=True)
        _edit(env)
        _write_chip(env["live"], _state(f01=7.7e9))
        r = c.post("/state/apply-to-live")
        trig = json.loads(r.headers.get("HX-Trigger") or "{}")
        return trig.get("autoSyncMerge") or {}

    def test_the_signal_names_the_chip_that_conflicted(self, env):
        sig = self._signal(env)
        assert sig.get("chip"), (
            "the merge signal names no chip, so the press cannot be held to one")
        with env["app"].app_context():
            assert sig["chip"] == routes_mod._active_chip_token()

    def test_the_door_refuses_a_press_naming_a_different_chip(self, env):
        self._signal(env)
        r = env["client"].post("/state/sync", data={
            "mode": "apply", "expect_chip": "some-other-chips-token"})
        assert r.status_code == 409, r.status_code
        body = r.get_json() or {}
        assert body.get("chip_mismatch") is True, body

    @pytest.mark.parametrize("collision", [False, True])
    def test_the_right_token_is_accepted(self, env, collision):
        # v1.5.1 (r6e): accepted overwrite -> accepted token with a separate collision refusal.
        sig = self._signal(env)
        # v1.5.1 (r6): collision-only -> also a landed merge, to pin token acceptance.
        if not collision:
            _write_chip(env["live"], _state(t1=4e-5))
        r = env["client"].post("/state/sync", data={
            "mode": "apply", "expect_chip": sig["chip"]})
        assert r.status_code == 200, (r.status_code, r.data[:200])
        assert (r.get_json() or {}).get("status") == ("collision" if collision else "ok"), r.get_json()
        assert _live_f01(env) == (7.7e9 if collision else 5.1e9)
        if not collision:
            assert json.loads((env["live"] / "state.json").read_text())["qubits"]["qA1"]["T1"] == 4e-5

    def test_a_caller_naming_nothing_is_judged_as_before(self, env):
        """Back-compatible: every existing presser sends no token."""
        self._signal(env)
        r = env["client"].post("/state/sync", data={"mode": "apply"})
        assert r.status_code == 200, r.status_code

    def test_the_client_hands_back_the_signal_token_not_the_page(self):
        """It must use e.detail.chip, not window.__chipToken -- reading the page
        at press time is exactly the race this closes."""
        js = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"
              / "static" / "auto-apply.js").read_text(encoding="utf-8")
        assert "var d = e.detail || {}; d.mode = 'apply'" in js
        assert "d.chip || '', { automatic: true }" in js

    def test_doStateSync_forwards_the_token(self):
        js = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"
              / "static" / "app.js").read_text(encoding="utf-8")
        i = js.index("window.doStateSync = function(")
        head = js[i:i + 200]
        assert "expectChip" in head, head
        # the whole function (a fixed 4000-char window stopped reaching the
        # fetch once the QA liveedit-r2-06 press queue grew the head)
        blk = js[i:js.index("\n};", i)]
        assert 'expect_chip=" + encodeURIComponent(expectChip)' in blk


class TestThePillSurvivesAHiddenTopBar:
    """docs/187 review R6. style.css:950 exempts DIRECT children of
    #pending-tray from the topbar-hidden hide rule:

        html.topbar-hidden #pending-tray
            > :not(.state-status-badge):not(.auto-sync-wrap) { display: none; }

    The first cut nested the conflict tray's pill inside `.tray-bar`, which IS
    a direct child and IS hidden — taking the pill with it. So with the top bar
    collapsed the tray pointed at a control nobody could see or click.
    """

    def _conflict(self, env):
        c = env["client"]
        _arm(env, pull=True, push=True)
        _edit(env)
        _write_chip(env["live"], _state(f01=7.7e9))
        return c.post("/state/apply-to-live").data.decode()

    def test_the_wrap_is_a_direct_child_of_the_tray(self, env):
        html = self._conflict(env)
        i = html.index('id="pending-tray"')
        body = html[html.index(">", i) + 1:]
        # walk the top level: depth 0 is a direct child
        depth, direct = 0, []
        import re
        for m in re.finditer(r"<(/?)(\w+)[^>]*?(/?)>", body):
            closing, tag, selfclose = m.group(1), m.group(2), m.group(3)
            if closing:
                depth -= 1
                if depth < 0:
                    break
                continue
            if depth == 0:
                direct.append(m.group(0))
            if not selfclose and tag not in ("br", "input", "img", "hr"):
                depth += 1
        assert any("auto-sync-wrap" in d for d in direct), (
            "the pill is nested, so topbar-hidden hides it with its parent: "
            + str([d[:60] for d in direct]))

    def test_the_css_rule_it_relies_on_is_still_there(self):
        """If that selector is ever rewritten, this pin must be re-read rather
        than the markup quietly re-nested."""
        css = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"
               / "static" / "style.css").read_text(encoding="utf-8")
        assert ("html.topbar-hidden #pending-tray > "
                ":not(.state-status-badge):not(.auto-sync-wrap)") in css


class TestAStagedPayloadIsNotMerged:
    """docs/187 review R4. `/state/sync?mode=apply` takes the docs/65 carve-out
    for a staged/saved payload and delegates WITHOUT pulling — re-issuing the
    push that just conflicted. So a merge signalled there can never succeed:
    measured, three futile rounds and then the same disarm, while the tray
    claimed to be resolving it, directly above its own staged branch which
    withholds pull-and-re-apply from the human on purpose.
    """

    def _staged_conflict(self, env):
        """A STAGED payload — `ctx['staged_base']`, set by a State-History
        stage or a dataset Load State. This is the class the docs/65 carve-out
        is about.

        Not `/save`: that fills `pending_reapply`, and with a stash the sync
        route pulls and replays normally, so a saved edit genuinely merges. My
        first fixture named that shape and was measuring the wrong thing.
        """
        c = env["client"]
        snap = c.post("/state-history/snapshot")
        assert snap.status_code in (200, 204, 302), snap.status_code
        with env["app"].app_context():
            snaps = routes_mod._history().list_snapshots(_ctx(env)["path"])
        assert snaps, "no snapshot to stage"
        ts = snaps[0].timestamp
        _arm(env, pull=True, push=True)
        st = c.post(f"/state-history/{ts}/stage", data={"force": "1"})
        assert st.status_code in (200, 204), st.status_code
        ctx = _ctx(env)
        assert ctx.get("staged_base"), (
            "the fixture did not reach the staged class — nothing here proves "
            "anything without it")
        _write_chip(env["live"], _state(f01=7.7e9))
        return c.post("/state/apply-to-live")

    def test_no_merge_is_signalled_for_a_staged_payload(self, env):
        r = self._staged_conflict(env)
        trig = r.headers.get("HX-Trigger", "")
        assert "autoSyncMerge" not in trig, (
            "signalled a merge the door it presses cannot perform — three "
            "futile rounds and then the same disarm")

    def test_it_disarms_honestly_instead(self, env):
        r = self._staged_conflict(env)
        assert "autoApplyDisarm" in r.headers.get("HX-Trigger", "")
        event = json.loads(r.headers["HX-Trigger"])["autoApplyDisarm"]
        assert event["class"] == "permanent" and event["tries"] == 1
        assert _sess(env) is None
        html = r.data.decode()
        assert "Auto-Sync is off" in html, "it did not say the session was off"
        assert "staged snapshot or wholesale working state" in html
        assert "auto-apply-off" in html

    def test_and_never_claims_to_be_resolving_it(self, env):
        html = self._staged_conflict(env).data.decode()
        assert "is resolving this itself" not in html, (
            "the tray claimed a merge was running above the branch that "
            "withholds that button from the human")

    def test_the_resolving_line_states_a_DECISION_not_the_session(self, env):
        """It used to be rendered from `auto_sync.pull`, so it also appeared on
        the /state/sync conflict door where no merge had been decided."""
        cf = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"
              / "templates" / "_state_apply_conflict.html").read_text(encoding="utf-8")
        assert "{% elif auto_merging %}" in cf, cf[:200]
        assert "auto_sync.pull" not in cf, (
            "the line is inferred from the session again")

    def test_a_plain_pending_edit_still_merges(self, env):
        """The ordinary case — the customer's — must be untouched."""
        c = env["client"]
        _arm(env, pull=True, push=True)
        _edit(env)
        _write_chip(env["live"], _state(f01=7.7e9))
        r = c.post("/state/apply-to-live")
        assert "autoSyncMerge" in r.headers.get("HX-Trigger", "")
        assert _sess(env) is not None
        assert "is resolving this itself" in r.data.decode()


class TestTheServerReportsEveryKindOfLoss:
    """The review-round sweep found both halves of R7/R10 unpinned on the
    SERVER side: my pins checked that the client composes an honest sentence
    from `saved`/`stash`/`dom`, and nothing checked that the server sends them.
    Drop those keys and the client's `more` goes falsy — a partial count
    presented as the whole loss, which is the defect itself.
    """

    def _replace_pull(self, env):
        with env["app"].test_request_context():
            ctx = routes_mod._active_ctx()
            ctx["_live_hash_checked_at"] = None
            routes_mod._refresh_live_diverged(ctx)
        r = env["client"].post("/auto-sync/pull", data={"dom_dirty": "0"})
        trig = r.headers.get("HX-Trigger", "")
        return r, (json.loads(trig).get("autoSyncPulled") if trig else None)

    def test_it_reports_saved_and_stashed_work_not_just_the_change_log(self, env):
        """A saved-but-unapplied working state is destroyed by the same pull.
        Reporting only the change log understates the loss."""
        c = env["client"]
        _arm(env, pull=True, push=True, replace=True)
        _edit(env, path="qubits.qA1.T1", value="9.99e-5")
        assert c.post("/save").status_code in (200, 204)      # -> working_dirty + stash
        _edit(env, path="qubits.qA1.f_01", value="5.25e9")    # -> change log too
        ctx = _ctx(env)
        assert ctx.get("working_dirty") and ctx.get("pending_reapply"), ctx.keys()
        _write_chip(env["live"], _state(f01=7.7e9))

        r, payload = self._replace_pull(env)
        assert r.status_code == 200, r.status_code
        assert payload, "no autoSyncPulled at all"
        assert payload.get("replaced") is True, payload
        assert "saved" in payload and "stash" in payload, (
            "the server reports only the change log, so the client cannot "
            "know the count is partial: %r" % payload)
        assert payload["saved"] is True, payload
        assert payload["stash"] >= 1, payload

    def test_work_that_lands_DURING_the_pull_is_kept(self, env):
        """Binding decision: work made inside a pull must survive. A deferred
        replace must never report that it discarded the preserved edit."""
        c = env["client"]
        _arm(env, pull=True, push=True, replace=True)
        _write_chip(env["live"], _state(f01=7.7e9))

        real = routes_mod.working_copy.sync_from_live
        landed = {}

        def _sync_then_edit(wc, *a, **k):
            out = real(wc, *a, **k)
            # the race, made deterministic: an edit arrives mid-pull
            landed["r"] = c.post("/field/edit", data={
                "dot_path": "qubits.qA1.T1", "value": "4.44e-5"}).status_code
            return out

        routes_mod.working_copy.sync_from_live = _sync_then_edit
        try:
            r, payload = self._replace_pull(env)
        finally:
            routes_mod.working_copy.sync_from_live = real

        assert landed.get("r") == 200, landed
        assert r.status_code == 204, r.status_code
        assert payload is None, "a deferred pull must not report discarded edits"
        ctx = _ctx(env)
        assert ctx["store"].state["qubits"]["qA1"]["T1"] == 4.44e-5
        assert [row.dot_path for row in ctx["store"].change_log] == ["qubits.qA1.T1"]
        wc = ctx["working_copy"]
        work = json.loads((Path(wc.working_folder) / "state.json").read_text(encoding="utf-8"))
        assert work["qubits"]["qA1"]["T1"] == 4.44e-5
        assert wc.synced_live_hash != routes_mod.working_copy.live_content_hash(wc)


class TestAPullAsksOnlyAboutTheFieldTheUserTouched:
    """The customer's rule: Auto-Sync adopts an external change by itself, and
    only stops to ask when the SAME field was edited on both sides.

    Before this, one whole-file question -- is the working copy dirty at all --
    meant an experiment writing T1 while the user edited f_01 raised exactly
    the prompt an experiment overwriting f_01 raised. Same box, different
    meaning, so the box stopped meaning anything.
    """

    def _armed_and_edited(self, env, *, path="qubits.qA1.f_01"):
        _arm(env, pull=True, push=False, replace=False)
        assert _edit(env, path=path).status_code == 200

    def test_a_different_field_merges_without_asking(self, env):
        # The user edited f_01; a node writes T1. Nothing collides.
        self._armed_and_edited(env)
        _write_chip(env["live"], _state(f01=5.0e9, t1=9.9e-5))
        _ctx(env)["live_diverged"] = True

        r = env["client"].post("/auto-sync/pull")
        # The server does not pull here itself -- it tells the client to press
        # the door that pulls AND puts the user's edits back on top.
        assert "autoSyncMergePull" in (r.headers.get("HX-Trigger") or ""), \
            "a non-colliding live change must resolve itself"
        assert not _ctx(env).get("live_conflicts")

    def test_the_same_field_disarms_with_a_named_blocker(self, env):
        # The user edited f_01; a node writes f_01 too. A real collision.
        self._armed_and_edited(env)
        _write_chip(env["live"], _state(f01=7.7e9))
        _ctx(env)["live_diverged"] = True

        r = env["client"].post("/auto-sync/pull")
        assert r.status_code == 200, "a collision must not resolve itself"
        assert "autoSyncMergePull" not in (r.headers.get("HX-Trigger") or "")
        assert _ctx(env)["live_conflicts"] == ["qubits.qA1.f_01"], \
            "and the refusal must NAME what collided"

        assert _sess(env) is None
        assert "overlap your edits" in r.get_data(as_text=True)

    def test_the_signal_names_the_chip(self, env):
        # Same reason docs/187 R2 put a chip on the push-side signal: the
        # client presses a door ~2s later, and a chip switch in between must
        # be a refusal, not a write onto the wrong chip.
        self._armed_and_edited(env)
        _write_chip(env["live"], _state(f01=5.0e9, t1=9.9e-5))
        _ctx(env)["live_diverged"] = True

        trig = json.loads(env["client"].post("/auto-sync/pull").headers["HX-Trigger"])
        assert trig["autoSyncMergePull"]["chip"], "the signal must name a chip"

    def test_a_typed_but_uncommitted_cell_is_judged_by_its_path(self, env):
        # The server cannot see a typed grid cell, so the client reports the
        # PATHS. A cell on a field the chip did not move is not a collision.
        _arm(env, pull=True, push=False, replace=False)
        _write_chip(env["live"], _state(f01=5.0e9, t1=9.9e-5))
        _ctx(env)["live_diverged"] = True

        r = env["client"].post("/auto-sync/pull", data={
            "dom_dirty": "1", "dom_path": "qubits.qA1.f_01"})
        assert "autoSyncMergePull" in (r.headers.get("HX-Trigger") or "")

    def test_a_typed_cell_on_the_moved_field_does_ask(self, env):
        _arm(env, pull=True, push=False, replace=False)
        _write_chip(env["live"], _state(f01=7.7e9))
        _ctx(env)["live_diverged"] = True

        r = env["client"].post("/auto-sync/pull", data={
            "dom_dirty": "1", "dom_path": "qubits.qA1.f_01"})
        assert r.status_code == 200
        assert _ctx(env)["live_conflicts"] == ["qubits.qA1.f_01"]

    def test_unnamed_dom_dirt_is_still_dirt(self, env):
        # A dirty cell carrying no dot-path must never read as "nothing typed":
        # the flag alone has to keep the old, conservative answer.
        _arm(env, pull=True, push=False, replace=False)
        _write_chip(env["live"], _state(f01=7.7e9))
        _ctx(env)["live_diverged"] = True

        r = env["client"].post("/auto-sync/pull", data={"dom_dirty": "1"})
        assert r.status_code == 204

    def test_a_clean_copy_is_untouched_by_any_of_this(self, env):
        # No edits at all -> the plain pull, exactly as before.
        _arm(env, pull=True, push=False, replace=False)
        _write_chip(env["live"], _state(f01=7.7e9))
        _ctx(env)["live_diverged"] = True

        r = env["client"].post("/auto-sync/pull", data={"dom_dirty": "0"})
        assert r.status_code == 200
        assert "autoSyncMergePull" not in (r.headers.get("HX-Trigger") or "")

    def test_a_verdict_that_could_not_be_computed_ASKS(self, env):
        """The one direction that must never fail open.

        If the live chip cannot be read, SM does not know whether anything
        collides -- and "no conflict" would mean pulling over the user's edits
        with no prompt and no Ctrl+Z. Unknown has to mean ask.
        """
        self._armed_and_edited(env)
        _write_chip(env["live"], _state(f01=5.0e9, t1=9.9e-5))  # a SAFE change
        _ctx(env)["live_diverged"] = True

        import quam_state_manager.web.routes as rt
        real = rt._auto_pull_verdict
        rt._auto_pull_verdict = lambda ctx, dom: None      # the read failed
        try:
            r = env["client"].post("/auto-sync/pull")
        finally:
            rt._auto_pull_verdict = real
        assert r.status_code == 200
        event = json.loads(r.headers["HX-Trigger"])["autoSyncRetry"]
        assert event["class"] == "transient"
        assert _sess(env) is not None
        assert "autoSyncMergePull" not in (r.headers.get("HX-Trigger") or "")

    def test_replace_still_means_replace(self, env):
        # The checkbox's own semantics are not touched by the new verdict: a
        # user who ticked replace asked for the live chip to win outright.
        self._armed_and_edited(env)
        _ctx(env)["auto_apply"]["pull_replace"] = True
        _write_chip(env["live"], _state(f01=7.7e9))
        _ctx(env)["live_diverged"] = True

        r = env["client"].post("/auto-sync/pull")
        assert r.status_code == 200, "replace pulls through the collision"
        assert "autoSyncMergePull" not in (r.headers.get("HX-Trigger") or "")

class TestTheSignalDoesNotWithholdAMergeableePull:
    """`_auto_pull_due` is what tells the client a pull is worth pressing.

    It carried the same whole-file "is anything dirty" test as the pull, for a
    good reason at the time: do not advertise a pull the policy will refuse, or
    the client POSTs a 204 every 5 s forever and each one takes the shared
    apply latch. But the policy stopped refusing the non-colliding case, so
    that guard became the thing preventing the resolution.
    """

    def _armed_edited_and_moved(self, env, *, same_field):
        _arm(env, pull=True, push=False, replace=False)
        assert _edit(env, path="qubits.qA1.f_01").status_code == 200
        if same_field:
            _write_chip(env["live"], _state(f01=7.7e9))
        else:
            _write_chip(env["live"], _state(f01=5.0e9, t1=9.9e-5))
        _ctx(env)["live_diverged"] = True

    def test_a_non_colliding_change_is_advertised(self, env):
        self._armed_edited_and_moved(env, same_field=False)
        with env["app"].app_context():
            assert routes_mod._auto_pull_due(_ctx(env)) is True

    def test_a_collision_is_advertised_once_then_falls_silent(self, env):
        # Once. The pull has to run to learn there IS a collision -- what must
        # not happen is it being re-advertised on every poll afterwards.
        self._armed_edited_and_moved(env, same_field=True)
        ctx = _ctx(env)
        with env["app"].app_context():
            assert routes_mod._auto_pull_due(ctx) is True, "the first look is allowed"
        assert env["client"].post("/auto-sync/pull").status_code == 200
        assert ctx["live_conflicts"] == ["qubits.qA1.f_01"]
        with env["app"].app_context():
            assert routes_mod._auto_pull_due(ctx) is False,                 "a known, unchanged collision must stop being advertised"

    def test_a_new_live_write_does_not_rearm_the_signal(self, env):
        # The remembered refusal is only valid while nothing moved. Another
        # write is a new question, and must be asked again.
        self._armed_edited_and_moved(env, same_field=True)
        assert env["client"].post("/auto-sync/pull").status_code == 200
        ctx = _ctx(env)
        with env["app"].app_context():
            assert routes_mod._auto_pull_due(ctx) is False
        time.sleep(0.02)
        _write_chip(env["live"], _state(f01=8.8e9))     # they write again
        with env["app"].app_context():
            assert routes_mod._auto_pull_due(ctx) is False

    def test_a_new_edit_does_not_rearm_it_too(self, env):
        self._armed_edited_and_moved(env, same_field=True)
        assert env["client"].post("/auto-sync/pull").status_code == 200
        ctx = _ctx(env)
        with env["app"].app_context():
            assert routes_mod._auto_pull_due(ctx) is False
        assert _edit(env, path="qubits.qA1.T1", value="3e-05").status_code == 200
        with env["app"].app_context():
            assert routes_mod._auto_pull_due(ctx) is False

    def test_undoing_the_colliding_edit_does_not_rearm_the_signal(self, env):
        """Ctrl+Z on the colliding edit leaves nothing to decide.

        (I first kept `len(change_log)` in the fingerprint believing undo did
        not move `mutation_seq`. It does -- the sweep's own guard assertion
        caught that, and the redundant term was removed. The BEHAVIOUR is still
        worth pinning: this is what a user actually does when told two writes
        collided.)
        """
        self._armed_edited_and_moved(env, same_field=True)
        assert env["client"].post("/auto-sync/pull").status_code == 200
        ctx = _ctx(env)
        with env["app"].app_context():
            assert routes_mod._auto_pull_due(ctx) is False
        assert env["client"].post("/undo").status_code in (200, 204)
        assert not ctx["store"].change_log, "the edit is gone"
        with env["app"].app_context():
            assert routes_mod._auto_pull_due(ctx) is False

    def test_a_resolved_verdict_is_forgotten(self, env):
        # The merge path clears the record. Leaving it behind would let a
        # later, unrelated state be judged by a verdict about an older one.
        self._armed_edited_and_moved(env, same_field=True)
        assert env["client"].post("/auto-sync/pull").status_code == 200
        ctx = _ctx(env)
        assert (ctx.get("live_auto_at") or {}).get("conflicts"),             "the refusal was remembered"

        time.sleep(0.02)
        _write_chip(env["live"], _state(f01=5.1e9, t1=9.9e-5))  # now agrees with the edit
        _arm(env, pull=True, push=False)  # explicit one-click way back on
        r = env["client"].post("/auto-sync/pull")
        assert "autoSyncMergePull" in (r.headers.get("HX-Trigger") or "")
        # The record SURVIVES now — it carries the merge budget — but it must
        # no longer claim a collision, or the banner would keep naming one.
        assert not (ctx.get("live_auto_at") or {}).get("conflicts"),             "a resolved state must not keep the refusal"

    def test_a_save_re_arms_the_signal(self, env):
        # The verdict before and after a save are different answers about the
        # same edits: saved-but-unapplied content has no originals in memory,
        # so the honest verdict becomes "cannot tell -- ask". The fingerprint
        # has to see that, and `mutation_seq` does not move for a save.
        self._armed_edited_and_moved(env, same_field=False)
        ctx = _ctx(env)
        with env["app"].app_context():
            sig_before = routes_mod._auto_pull_sig(ctx)
        ctx["working_dirty"] = True              # what /save records
        with env["app"].app_context():
            sig_after = routes_mod._auto_pull_sig(ctx)
        assert sig_before != sig_after, \
            "a save changes the answer, so it must change the fingerprint"

    def test_a_clean_copy_is_advertised_exactly_as_before(self, env):
        _arm(env, pull=True, push=False, replace=False)
        _write_chip(env["live"], _state(f01=7.7e9))
        _ctx(env)["live_diverged"] = True
        with env["app"].app_context():
            assert routes_mod._auto_pull_due(_ctx(env)) is True

    def test_an_unarmed_session_is_never_advertised(self, env):
        _write_chip(env["live"], _state(f01=7.7e9))
        _ctx(env)["live_diverged"] = True
        with env["app"].app_context():
            assert routes_mod._auto_pull_due(_ctx(env)) is False

class TestTheBannerNamesTheCollision:
    """A banner that appears for everything says nothing.

    Since a non-colliding live change now merges silently, an appearance of
    this banner means a genuine two-sided edit -- so it names the fields
    instead of counting values over a whole chip.
    """

    def test_it_names_the_colliding_fields(self, env):
        _arm(env, pull=True, push=False, replace=False)
        assert _edit(env, path="qubits.qA1.f_01").status_code == 200
        _write_chip(env["live"], _state(f01=7.7e9))
        _ctx(env)["live_diverged"] = True
        assert env["client"].post("/auto-sync/pull").status_code == 200

        html = env["client"].get("/bulk").get_data(as_text=True)
        # sync-ux 2026-09-25 (user decision: one control + one panel): the status control names it (the banner is gone)
        assert "qubits.qA1.f_01" in html, "the control must name what collided"
        assert "changed on both sides" in html and 'data-sync-state="collide"' in html

    def test_with_no_verdict_it_reads_exactly_as_before(self, env):
        # Nothing established a per-field verdict (no armed session), so the
        # historical wording stands -- this change adds a case, never replaces
        # the old one.
        _write_chip(env["live"], _state(f01=7.7e9))
        _ctx(env)["live_diverged"] = True
        html = env["client"].get("/bulk").get_data(as_text=True)
        # sync-ux 2026-09-25 (user decision: one control + one panel): the control's plain live-changed wording
        assert "Live chip changed" in html
        assert "changed on both sides" not in html

class TestTheMergeSignalHasABudget:
    def test_pull_signal_is_backed_off_without_disarming(self, env, monkeypatch):
        _arm(env, pull=True, push=False)
        _edit(env)
        _write_chip(env["live"], _state(f01=5.0e9, t1=9.9e-5))
        ctx = _ctx(env)
        ctx["live_diverged"] = True
        now = [1000.0]
        original_time = routes_mod.time
        class Clock:
            def monotonic(self):
                return now[0]
            def __getattr__(self, key):
                return getattr(original_time, key)
        monkeypatch.setattr(routes_mod, "time", Clock())
        for n, delay in enumerate((2, 5, 15, 30, 60, 60), 1):
            r = env["client"].post("/auto-sync/pull")
            event = json.loads(r.headers["HX-Trigger"])["autoSyncMergePull"]
            assert event["class"] == "transient"
            assert event["delay"] == delay
            assert event["mode"] == "reapply"
            assert _sess(env) is not None
            with env["app"].app_context():
                assert not routes_mod._auto_pull_due(ctx)
            blocked = env["client"].post("/auto-sync/pull")
            assert "autoSyncMergePull" not in blocked.headers.get("HX-Trigger", "")
            now[0] += delay
            with env["app"].app_context():
                assert routes_mod._auto_pull_due(ctx)

    def test_new_live_write_does_not_shorten_backoff(self, env):
        _arm(env, pull=True, push=False)
        _edit(env)
        _write_chip(env["live"], _state(f01=5.0e9, t1=9.9e-5))
        ctx = _ctx(env)
        ctx["live_diverged"] = True
        env["client"].post("/auto-sync/pull")
        _write_chip(env["live"], _state(f01=5.0e9, t1=7.7e-5))
        with env["app"].app_context():
            assert not routes_mod._auto_pull_due(ctx)
        assert _sess(env) is not None


class TestFailureKinds:
    def test_five_locks_then_landing(self, env, monkeypatch):
        _arm(env)
        _edit(env)
        original = routes_mod.working_copy.apply_to_live
        def locked(*a, **kw):
            raise OSError("[WinError 32] sharing violation on replace")
        monkeypatch.setattr(routes_mod.working_copy, "apply_to_live", locked)
        for n in range(1, 6):
            # the AUTOMATIC merge's own press (walk-int review: the policy
            # judges automatic requests; a person's press is answered to them)
            _sess(env)["merge_due"] = 0          # the backoff has elapsed
            r = env["client"].post("/state/sync", data={"mode": "apply", "auto_sync": "1"})
            assert _sess(env) is not None
            assert _sess(env)["merge_tries"] == n
            event = json.loads(r.headers["HX-Trigger"])["autoSyncRetry"]
            assert event["class"] == "transient"
            assert f"retrying ({n})" in r.get_json()["tray_html"]
        monkeypatch.setattr(routes_mod.working_copy, "apply_to_live", original)
        _sess(env)["unknown_tries"] = 3
        _sess(env)["merge_due"] = 0
        r = env["client"].post("/state/sync", data={"mode": "apply", "auto_sync": "1"})
        assert r.get_json()["status"] == "ok"
        assert _sess(env)["merge_tries"] == 0
        assert not _sess(env).get("merge_retry")
        assert not _sess(env).get("unknown_tries")

    def test_unknown_cap(self, env, monkeypatch):
        _arm(env)
        _edit(env)
        def broken(*a, **kw):
            raise RuntimeError("unexpected synthetic failure")
        monkeypatch.setattr(routes_mod.working_copy, "apply_to_live", broken)
        for n in range(1, 11):
            if _sess(env) is not None:
                _sess(env)["merge_due"] = 0      # the backoff has elapsed
            r = env["client"].post("/state/sync", data={"mode": "apply", "auto_sync": "1"})
            assert (_sess(env) is None) == (n == 10)
        assert "kept failing:" in r.get_json()["tray_html"]
        assert "unexpected synthetic failure" in r.get_json()["tray_html"]


@pytest.mark.parametrize("reason,code,expected", [
    ("[WinError 32] replace", "", "transient"),
    ("[WinError 33] replace", "", "transient"),
    ("[WinError 5] replace", "", "unknown"),
    (routes_mod.safe_io.LiveFileError("retries exhausted"), "", "transient"),
    ("mid-save", "torn_live", "transient"),
    ("moved again", "stale_live", "transient"),
    ("still writing", "live_press", "transient"),
    ("staged snapshot", "staged", "permanent"),
    (PermissionError("denied"), "", "permanent"),
    ("read-only folder", "readonly", "permanent"),
    ("wrong chip", "chip_mismatch", "permanent"),
    ("archive", "archive", "permanent"),
    ("another live window", "foreign_owner", "permanent"),
    ("choose values", "collision", "permanent"),
    ("synthetic error", "", "unknown"),
])
def test_failure_classification(reason, code, expected):
    from quam_state_manager.core.merge_policy import classify_failure
    assert classify_failure(reason, code=code) == expected


@pytest.mark.parametrize("door", ["sync", "pull"])
def test_automatic_collision_keeps_mine_and_saves_node(env, door):
    _arm(env)
    _edit(env)
    _write_chip(env["live"], _state(f01=7.7e9))
    _ctx(env)["live_diverged"] = True
    r = env["client"].post("/state/sync" if door == "sync" else "/auto-sync/pull",
                           data={"mode": "apply", "auto_sync": "1"})
    assert _sess(env) is not None
    assert _live_f01(env) == 5.1e9
    assert _sess(env)["merge_tries"] == 0
    assert "pending-tray-conflict" not in r.get_data(as_text=True)
    events = json.loads(r.headers["HX-Trigger"])
    assert "liveConflict" not in events and "autoApplyDisarm" not in events
    notice = events["syncToast"]["message"]
    assert "kept your value for qA1.f_01" in notice
    assert "7.7" in notice and "GHz" in notice and "in History" in notice
    bk = _ctx(env)["take_live_backup"]
    assert bk["updates"]["qubits.qA1.f_01"][1] == 7.7e9
    with env["app"].app_context():
        hm = routes_mod._history()
        snaps = hm.list_snapshots(str(env["live"]))
        snap = next(s for s in snaps if s.timestamp == bk["snapshot"])
        assert snap.kind == "backup"
    held = [json.loads(p.read_text(encoding="utf-8")) for p in
            (Path(env["app"].instance_path) / "history").rglob("state.json")]
    assert any(d.get("qubits", {}).get("qA1", {}).get("f_01") == 7.7e9 for d in held)
    # The existing version-panel Bring back door restores the node in one press.
    env["client"].post("/state/take-live/restore")
    assert _ctx(env)["store"].state["qubits"]["qA1"]["f_01"] == 7.7e9


def test_push_only_collision_still_disarms_no_pull(env):
    """Without pull permission the collision is not ours to settle (docs/117)."""
    _arm(env, pull=False, push=True)
    _edit(env)
    _write_chip(env["live"], _state(f01=7.7e9))
    _ctx(env)["live_diverged"] = True
    r = env["client"].post("/state/sync", data={"mode": "apply", "auto_sync": "1"})
    assert _sess(env) is None
    assert _live_f01(env) == 7.7e9
    assert "take_live_backup" not in _ctx(env)
    assert "kept your value" not in r.headers.get("HX-Trigger", "")


def test_mid_session_readonly_disarms_first_attempt(env, monkeypatch):
    _arm(env)
    _edit(env)
    assert not _ctx(env).get("live_readonly_hint")
    def denied(*a, **kw):
        exc = PermissionError("synthetic ACL denial")
        exc.winerror = 5
        raise exc
    monkeypatch.setattr(routes_mod.working_copy, "apply_to_live", denied)
    monkeypatch.setattr(routes_mod, "_write_denial", lambda *a: "denied")
    r = env["client"].post("/state/sync", data={"mode": "apply", "auto_sync": "1"})
    assert _sess(env) is None
    assert _ctx(env)["live_readonly_hint"] is True
    verdict = r.get_json()["merge_failure"]
    assert verdict["class"] == "permanent" and verdict["tries"] == 1
    assert "read-only" in verdict["reason"]


def test_unseen_window_rereads_then_flushes(env):
    _arm(env)
    a, b = env["client"], env["app"].test_client()
    _edit(env)  # A edits the ctx shared with B, whose tray still says zero.
    r = b.post("/state/sync", data={"mode": "apply", "auto_sync": "1", "seen_changes": "0"})
    assert _sess(env) is not None
    verdict = r.get_json()["merge_failure"]
    assert verdict["class"] == "transient"
    assert verdict["refresh_tray"] is True
    assert _live_f01(env) == 5.0e9
    import re
    tray = b.get("/state/tray").get_data(as_text=True)
    count = re.search(r'data-change-count="(\d+)"', tray).group(1)
    assert count == "1"
    _sess(env)["merge_due"] = 0
    r = b.post("/state/sync", data={"mode": "apply", "auto_sync": "1", "seen_changes": count})
    assert r.get_json()["status"] == "ok"
    assert _live_f01(env) == 5.1e9 and _sess(env) is not None


@pytest.mark.parametrize("reason,code,expected", [
    ("failed in D:/archive/chip", "", "unknown"),
    ("[WinError 5] replace", "", "unknown"),
    ("archive", "archive", "permanent"),
    ("read-only comparison", "verdict_failed", "unknown"),
    ("another window has edits", "unseen_changes", "transient"),
])
def test_structured_failure_precedes_text(reason, code, expected):
    from quam_state_manager.core.merge_policy import classify_failure
    assert classify_failure(reason, code=code) == expected


def test_automatic_retry_after_disarm_cannot_write(env, monkeypatch):
    _edit(env)
    called = []
    monkeypatch.setattr(routes_mod.working_copy, "apply_to_live", lambda *a, **kw: called.append(True))
    r = env["client"].post("/state/sync", data={"mode": "apply", "auto_sync": "1"})
    assert r.status_code == 409
    assert r.get_json()["status"] == "stopped"
    assert not called


@pytest.mark.parametrize("winerror,expected", [(None, "permanent"), (5, "transient"), (32, "transient")])
def test_safe_io_preserves_the_os_denial(winerror, expected):
    from quam_state_manager.core.merge_policy import classify_failure
    cause = PermissionError("synthetic folder denial")
    if winerror is not None:
        cause.winerror = winerror
    wrapped = routes_mod.safe_io.LiveFileError("replace retries exhausted")
    wrapped.__cause__ = cause
    assert classify_failure(wrapped) == expected
