# -*- coding: utf-8 -*-
"""docs/187 — a push that finds the chip moved must MERGE, not turn itself off.

Customer, on-site (2026-09-12): "live edit에서 enter를 누르면 그 다음부터
auto sync가 깨짐", and "이유없이 pull&apply 문구가 뜬다".

Their bench is the reason: SM (pid 4928, :5050) has
``D:\\work\\Customer_Codes\\quam_states\\260907_KRS_5Q`` open, and that is
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
        assert ctx.get("working_dirty") or ctx.get("pending_reapply"), (
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

    def test_it_gives_up_and_disarms_after_the_budget(self, env):
        c = env["client"]
        _arm(env, pull=True, push=True)
        seen = []
        for i in range(routes_mod._AUTO_MERGE_TRIES + 1):
            _edit(env, value=f"5.{i}e9")
            _write_chip(env["live"], _state(f01=7.0e9 + i))
            r = c.post("/state/apply-to-live")
            seen.append(r.headers.get("HX-Trigger", ""))
            if _sess(env) is None:
                break
        assert _sess(env) is None, (
            "a merge that never succeeds must not keep the session armed")
        assert "autoApplyDisarm" in seen[-1]
        assert sum("autoSyncMerge" in h for h in seen) == \
            routes_mod._AUTO_MERGE_TRIES, seen

    def test_an_apply_that_lands_refills_the_budget(self, env):
        """The budget counts CONSECUTIVE failures — otherwise a long, healthy
        session would eventually disarm on its Nth unlucky node write."""
        c = env["client"]
        _arm(env, pull=True, push=True)
        _edit(env)
        _write_chip(env["live"], _state(f01=7.7e9))
        c.post("/state/apply-to-live")
        assert _sess(env)["merge_tries"] == 1

        # resolve it the way the client does, then a clean flush
        c.post("/state/sync", data={"mode": "apply"})
        _edit(env, value="5.5e9")
        c.post("/state/apply-to-live")
        assert _sess(env)["merge_tries"] == 0, (
            "a landed apply proves the chip is reachable again")

    def test_the_MERGE_itself_refills_the_budget(self, env):
        """R1, from the review round: this is the one that matters, and the pin
        above could not see it.

        The budget was cleared only in /state/apply-to-live's tail, while the
        merge the autoSyncMerge signal asks for goes through the OTHER door
        (/state/sync?mode=apply). The pin above resolves the conflict and then
        does an EXTRA clean apply-to-live -- the one path that resets -- so it
        was satisfied by a door the bench never takes.

        This is the bench loop: edit, a node writes, flush, merge. Nothing
        else. On the customer's chip that is every 30-60 seconds, and with the
        budget counting conflicts instead of failures the session disarmed on
        the fourth node write with all three merges having LANDED.
        """
        c = env["client"]
        _arm(env, pull=True, push=True)
        landed = []
        for i in range(routes_mod._AUTO_MERGE_TRIES + 2):
            _edit(env, path="qubits.qA1.T1", value=f"1.{i}e-5")
            _write_chip(env["live"], _state(f01=7.0e9 + i))   # the node
            r = c.post("/state/apply-to-live")
            if _sess(env) is None:
                break
            assert "autoSyncMerge" in r.headers.get("HX-Trigger", ""), i
            # what the client does with that signal, and nothing more
            m = c.post("/state/sync", data={"mode": "apply"})
            assert m.status_code == 200, (i, m.data[:200])
            assert (m.get_json() or {}).get("status") == "ok", (i, m.get_json())
            doc = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
            landed.append(doc["qubits"]["qA1"]["T1"])

        assert _sess(env) is not None, (
            "Auto-Sync disarmed after %d merges that all LANDED — the budget is "
            "counting conflicts, not failures, so the reported bug returns a "
            "few edits later (values on the chip: %r)" % (len(landed), landed)
        )
        assert len(landed) == routes_mod._AUTO_MERGE_TRIES + 2, landed
        assert _sess(env)["merge_tries"] == 0, _sess(env)
        # and every one of them really reached the chip
        assert landed == [float(f"1.{i}e-5") for i in range(len(landed))], landed


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
        if not (root / "node_modules" / "jsdom").exists():
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
        for name in ("autoSyncMerge", "autoSyncPulled"):
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
        tray = (root / "templates" / "_state_apply_conflict.html").read_text(encoding="utf-8")
        js = (root / "static" / "auto-apply.js").read_text(encoding="utf-8")
        assert "doStateSync('apply')" in tray, (
            "the tray no longer offers the merge the automatic path presses")
        i = js.index("addEventListener('autoSyncMerge'")
        assert "doStateSync('apply'" in js[i:i + 900], js[i:i + 400]


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
        assert "has been turned" not in html, (
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

    def test_the_right_token_is_accepted(self, env):
        sig = self._signal(env)
        r = env["client"].post("/state/sync", data={
            "mode": "apply", "expect_chip": sig["chip"]})
        assert r.status_code == 200, (r.status_code, r.data[:200])
        assert (r.get_json() or {}).get("status") == "ok", r.get_json()

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
        i = js.index("document.addEventListener('autoSyncMerge'")
        blk = js[i:i + 700]
        assert "e.detail && e.detail.chip" in blk, blk[:300]
        assert "__chipToken" not in blk, (
            "it reads the page's current chip, which defeats the gate")
        assert "doStateSync('apply', false, false, chip)" in blk, blk[:400]

    def test_doStateSync_forwards_the_token(self):
        js = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"
              / "static" / "app.js").read_text(encoding="utf-8")
        i = js.index("window.doStateSync = function(")
        head = js[i:i + 200]
        assert "expectChip" in head, head
        blk = js[i:i + 4000]
        assert 'expect_chip=" + encodeURIComponent(expectChip)' in blk
