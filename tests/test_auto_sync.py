"""Auto-Sync: the covenant amended a second time (docs/120 item 8).

The user, 2026-08-16: *"many mature users run VS Code with auto-save on. When
qualibrate updates, VS Code always gets the SYNCED file. But SM's original
design concept was: never pull/push the source of truth without the user's
permission. It's time to drop that. What users want is auto pull/push WHEN THEY
ALLOW IT. Push is done; now pull."*

And the line they drew, verbatim: *"if 1-1 is checked, that IS the user agreeing
that a pull may just replace silently, so live ALWAYS wins. If it isn't checked,
then you have to tell them. With a banner."*

The whole feature is that table, so this file is that table:

    pull  replace  local edits   behaviour
    ----  -------  -----------   ------------------------------------------
    on    on       either        live wins, silently
    on    off      no            pull silently (a provably clean copy)
    on    off      YES           do NOT pull; the drift banner asks
    off   -        -             byte-identical to today
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


def _mk(tmp_path):
    live = tmp_path / "quam_state"
    live.mkdir(parents=True, exist_ok=True)
    (live / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"id": "q1", "f_01": 6.0e9}},
         "qubit_pairs": {}, "active_qubit_names": ["q1"]}), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(
        {"network": {"host": "1.1.1.1"}, "wiring": {"qubits": {}}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(live)})
    return app, c, live


def _ctx(app):
    return (app.config.get("contexts") or {}).get(app.config.get("active_context"))


def _f01(app):
    return _ctx(app)["store"].merged["qubits"]["q1"]["f_01"]


def _write_live(live: Path, f01: float):
    d = json.loads((live / "state.json").read_text(encoding="utf-8"))
    d["qubits"]["q1"]["f_01"] = f01
    (live / "state.json").write_text(json.dumps(d), encoding="utf-8")


def _diverge(app, live, f01):
    """Live moved out from under us. The flag is what the throttled ground-truth
    check maintains in production; setting it directly keeps this test about the
    POLICY rather than about detection timing."""
    _write_live(live, f01)
    _ctx(app)["live_diverged"] = True


class TestThePolicyTable:
    def test_off_is_byte_identical_to_today(self, tmp_path):
        """The load-bearing row: with nothing armed, SM behaves exactly as it
        did before this feature existed."""
        app, c, live = _mk(tmp_path)
        _diverge(app, live, 6.1e9)
        assert c.post("/auto-sync/pull").status_code == 204
        assert _f01(app) == 6.0e9
        assert _ctx(app)["live_diverged"] is True     # the banner still asks

    def test_replace_ticked_means_live_wins_silently(self, tmp_path):
        """Ticking it IS the consent -- the one configuration where SM discards
        the user's work without a question, because they said so."""
        app, c, live = _mk(tmp_path)
        c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "1", "push": "1"})
        c.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "5.5e9"})
        assert _f01(app) == 5.5e9
        _diverge(app, live, 6.2e9)
        assert c.post("/auto-sync/pull").status_code == 200
        assert _f01(app) == 6.2e9, "live wins"

    def test_replace_unticked_refuses_when_there_are_local_edits(self, tmp_path):
        """docs/87 intact: SM does not choose between your work and the chip."""
        app, c, live = _mk(tmp_path)
        c.post("/auto-sync/set", data={"pull": "1", "push": "1"})
        c.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "5.5e9"})
        _diverge(app, live, 6.3e9)
        assert c.post("/auto-sync/pull").status_code == 204
        assert _f01(app) == 5.5e9, "the user's edit survived"
        assert _ctx(app)["live_diverged"] is True, "and the banner is still asking"

    def test_a_clean_copy_pulls_without_asking(self, tmp_path):
        """Nothing of the user's is at stake, so there is nothing to ask about
        -- this is today's RECONCILE_SYNCED, unchanged."""
        app, c, live = _mk(tmp_path)
        c.post("/auto-sync/set", data={"pull": "1", "push": "1"})
        _diverge(app, live, 6.4e9)
        assert c.post("/auto-sync/pull").status_code == 200
        assert _f01(app) == 6.4e9


class TestArmabilityIsPerDirection:
    def test_a_diverged_chip_can_still_arm_pull(self, tmp_path):
        """The push gate refuses on live_diverged -- arming into a guaranteed
        conflict. For PULL that is the very condition that makes it useful, so
        reusing the push gate would disarm the feature exactly when wanted."""
        app, c, live = _mk(tmp_path)
        _diverge(app, live, 6.1e9)
        r = c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "1"})
        assert r.status_code == 200
        assert _ctx(app)["auto_apply"]["pull"] is True

    def test_a_diverged_chip_still_refuses_push(self, tmp_path):
        app, c, live = _mk(tmp_path)
        _diverge(app, live, 6.1e9)
        assert c.post("/auto-sync/set", data={"push": "1"}).status_code == 409

    def test_a_readonly_live_folder_blocks_push_but_not_pull(self, tmp_path):
        """A pull only READS live and writes the working copy."""
        app, c, live = _mk(tmp_path)
        _ctx(app)["live_readonly_hint"] = True
        assert c.post("/auto-sync/set", data={"push": "1"}).status_code == 409
        assert c.post("/auto-sync/set", data={"pull": "1"}).status_code == 200

    def test_an_archive_refuses_both(self, tmp_path):
        app, c, live = _mk(tmp_path)
        _ctx(app)["origin"] = "dataset_archive"
        assert c.post("/auto-sync/set", data={"push": "1"}).status_code == 409
        assert c.post("/auto-sync/set", data={"pull": "1"}).status_code == 409


class TestSessionShape:
    def test_everything_off_clears_the_session(self, tmp_path):
        """"Off" must be the absence of a session, not an armed one that
        happens to do nothing -- otherwise the tray would claim a mode."""
        app, c, _ = _mk(tmp_path)
        c.post("/auto-sync/set", data={"pull": "1", "push": "1"})
        assert _ctx(app).get("auto_apply")
        c.post("/auto-sync/set", data={})
        assert _ctx(app).get("auto_apply") is None

    def test_replace_without_pull_is_not_a_state(self, tmp_path):
        app, c, _ = _mk(tmp_path)
        c.post("/auto-sync/set", data={"pull_replace": "1", "push": "1"})
        assert _ctx(app)["auto_apply"]["pull_replace"] is False

    def test_a_pull_only_session_never_reads_as_armed_to_the_pusher(self, tmp_path):
        """The flusher's data-auto-apply beacon comes from _auto_apply_state.
        A pull-only session showing there would make the client start WRITING
        to the live chip on a permission that was never granted."""
        from quam_state_manager.web import routes as R
        app, c, _ = _mk(tmp_path)
        c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "1"})
        with app.test_request_context():
            assert R._auto_sync_state(_ctx(app)) is not None
            assert R._auto_apply_state(_ctx(app)) is None
        body = c.get("/state/tray").get_data(as_text=True)
        assert 'data-auto-apply="1"' not in body

    def test_push_only_still_arms_the_pusher(self, tmp_path):
        app, c, _ = _mk(tmp_path)
        c.post("/auto-sync/set", data={"push": "1"})
        assert 'data-auto-apply="1"' in c.get("/state/tray").get_data(as_text=True)

    def test_the_session_is_never_persisted(self, tmp_path):
        """docs/117: an armed session must not outlive the window that armed
        it. Nothing about it may reach disk."""
        app, c, _ = _mk(tmp_path)
        c.post("/auto-sync/set", data={"pull": "1", "push": "1"})
        for p in (tmp_path / "_i").rglob("*.json"):
            assert "auto_apply" not in p.read_text(encoding="utf-8", errors="ignore")
            assert "pull_replace" not in p.read_text(encoding="utf-8", errors="ignore")


class TestTheSignalRidesTheExistingPoll:
    def test_drift_carries_the_pull_flag(self, tmp_path):
        """No new poller (docs/110). /state/drift already runs on every page."""
        app, c, live = _mk(tmp_path)
        assert c.get("/state/drift").get_json().get("auto_pull") is False
        c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "1"})
        _diverge(app, live, 6.1e9)
        assert c.get("/state/drift").get_json().get("auto_pull") is True

    def test_the_flag_is_present_even_on_the_untracked_branch(self, tmp_path):
        """A chip with no drift baseline can still have diverged; dropping the
        flag there would make auto-pull work only for tracked chips."""
        app, c, live = _mk(tmp_path)
        c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "1"})
        _diverge(app, live, 6.1e9)
        _ctx(app).pop("drift_baseline", None)
        assert "auto_pull" in c.get("/state/drift").get_json()

    def test_the_client_shares_the_apply_in_flight_latch(self):
        """A pull must never interleave with a push or a manual Apply."""
        src = (Path(__file__).resolve().parent.parent / "quam_state_manager"
               / "web" / "static" / "app.js").read_text(encoding="utf-8")
        i = src.index("/auto-sync/pull")
        chunk = src[i - 2600:i + 600]
        assert "_applyInFlight" in chunk
        # ...and it must always be released, including when the request never
        # settles — the same latch gates the manual Apply buttons, so a wedged
        # pull would make them read as dead clicks (the docs/80 lesson).
        assert "setTimeout(_rel" in chunk
        # ...and releasing must poke the push flusher, whose queued work is
        # drained only by its own completion handler.
        assert "AutoApply.drain" in chunk

    def test_no_new_poller_was_added(self):
        src = (Path(__file__).resolve().parent.parent / "quam_state_manager"
               / "web" / "static" / "app.js").read_text(encoding="utf-8")
        # the pull is issued from inside the existing drift poll's handler
        i = src.index("/auto-sync/pull")
        assert "/state/drift" in src[max(0, i - 4000):i]


class TestFailuresDisarm:
    def test_an_unreadable_live_folder_disarms_rather_than_retrying(self, tmp_path):
        """Every poll would otherwise retry a pull that cannot work -- the same
        rule the push side already follows."""
        app, c, live = _mk(tmp_path)
        c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "1"})
        _diverge(app, live, 6.1e9)
        (live / "state.json").unlink()
        r = c.post("/auto-sync/pull")
        assert r.status_code in (204, 200)
        assert _ctx(app).get("auto_apply") is None, "the session was cleared"
        # ...and the client stops WITHOUT needing to have received the disarm
        # trigger. The signal is derived from the session, so clearing it is
        # self-healing: a 204 whose HX-Trigger the client never processed
        # cannot leave a pull spinning every 5 s.
        assert c.get("/state/drift").get_json().get("auto_pull") is False


class TestSessionsDoNotLeakAcrossChips:
    def test_arming_one_chip_never_arms_another(self, tmp_path):
        """The session lives on the chip's context. Arming chip A and then
        opening chip B must not authorize writes to B — that would be a
        permission granted for one device applied to a different one."""
        a, c, _ = _mk(tmp_path / "a")
        c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "1", "push": "1"})
        assert _ctx(a)["auto_apply"]

        other = tmp_path / "b" / "quam_state"
        other.mkdir(parents=True)
        (other / "state.json").write_text(json.dumps(
            {"qubits": {"q1": {"id": "q1", "f_01": 7.0e9}},
             "qubit_pairs": {}, "active_qubit_names": ["q1"]}), encoding="utf-8")
        (other / "wiring.json").write_text(json.dumps(
            {"network": {"host": "2.2.2.2"}, "wiring": {"qubits": {}}}), encoding="utf-8")
        c.post("/load", data={"folder": str(other)})

        assert _ctx(a).get("auto_apply") is None, "chip B is not armed"
        assert 'data-auto-apply="1"' not in c.get("/state/tray").get_data(as_text=True)
        assert c.get("/state/drift").get_json().get("auto_pull") is False


class TestTheTraySaysWhichModeIsOn:
    def test_the_pill_states_the_mode_not_just_on(self, tmp_path):
        """Pull and push are different promises; a user must be able to see
        which ones they granted."""
        app, c, _ = _mk(tmp_path)
        c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "1", "push": "1"})
        assert "both" in c.get("/state/tray").get_data(as_text=True)
        c.post("/auto-sync/set", data={"push": "1"})
        assert "push" in c.get("/state/tray").get_data(as_text=True)
        c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "1"})
        assert "pull" in c.get("/state/tray").get_data(as_text=True)

    def test_an_asking_pull_says_so(self, tmp_path):
        app, c, _ = _mk(tmp_path)
        c.post("/auto-sync/set", data={"pull": "1"})
        assert "asks before replacing" in c.get("/state/tray").get_data(as_text=True)

    def test_the_three_switches_default_to_on(self, tmp_path):
        """The user's call: all checked, uncheck what you don't want.

        The switches are their own fragment now, fetched when the pill is
        clicked — they used to live inside #pending-tray, which OOB-swaps on
        every commit, so a flush mid-configuration destroyed a partial choice.
        """
        app, c, _ = _mk(tmp_path)
        body = c.get("/auto-sync/panel").get_data(as_text=True)
        for cid in ("as-pull", "as-pull-replace", "as-push"):
            i = body.index(f'id="{cid}"')
            assert "checked" in body[i:i + 200], cid

    def test_the_switches_are_not_inside_the_swapped_tray(self, tmp_path):
        """Reducing permissions must not be able to fail silently."""
        app, c, _ = _mk(tmp_path)
        assert 'id="auto-sync-pop"' not in c.get("/state/tray").get_data(as_text=True)
        assert 'id="auto-sync-pop"' in c.get("/auto-sync/panel").get_data(as_text=True)

    def test_the_panel_shows_the_session_that_is_actually_armed(self, tmp_path):
        app, c, _ = _mk(tmp_path)
        c.post("/auto-sync/set", data={"pull": "1"})     # replace OFF
        body = c.get("/auto-sync/panel").get_data(as_text=True)
        i = body.index('id="as-pull-replace"')
        assert "checked" not in body[i:i + 200]


class TestTheRedTeamFindings:
    """Each of these is a way the first cut destroyed work or lied. They are
    pinned by the scenario that produced them, not by the patch that fixed
    them."""

    def test_the_dirty_check_is_repeated_inside_the_build_lock(self, tmp_path):
        """/field/edit takes only store._lock, and the window between the
        outer check and store.reload() spans lock acquisition plus two live
        reads and two working-folder writes — it opens exactly when an
        experiment just wrote the chip, i.e. when someone is mid-edit. An edit
        landing there was destroyed with "replace" UNCHECKED."""
        from quam_state_manager.web import routes as R
        src = Path(R.__file__).read_text(encoding="utf-8")
        i = src.index("def auto_sync_pull")
        body = src[i:i + 4600]
        lock_at = body.index("with build_lock:")
        after = body[lock_at:]
        assert "_quam_ctx_dirty(ctx)" in after, "no re-check inside the lock"

    def test_a_replace_pull_snapshots_before_discarding(self, tmp_path):
        """The change log is not journalled (the journal captures on save) and
        the redo stack self-invalidates, so without this the discarded work
        existed NOWHERE — while the popup promised revertibility."""
        app, c, live = _mk(tmp_path)
        c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "1"})
        c.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "5.5e9"})
        _diverge(app, live, 6.9e9)
        assert c.post("/auto-sync/pull").status_code == 200
        body = c.get("/state/versions").get_data(as_text=True)
        assert 'class="sv-check"' in body, "the pre-pull state is recoverable"

    def test_the_reapply_stash_is_cleared(self, tmp_path):
        """Every other _rebuild_after_working_copy_replaced caller pairs it with
        _clear_reapply. Leaving the stash lets a later "Pull & apply (merge)"
        replay the very values the user was told had been replaced — onto the
        chip."""
        from quam_state_manager.web import routes as R
        src = Path(R.__file__).read_text(encoding="utf-8")
        i = src.index("def auto_sync_pull")
        # Sliced generously: the function grew when the post-I/O re-check
        # landed, and a slice too short reads as "the call is gone".
        body = src[i:i + 9000]
        assert "_clear_reapply(ctx)" in body

    def test_typed_but_uncommitted_cells_block_a_non_replace_pull(self, tmp_path):
        """A fill-down or pasted column lives only in the DOM until Apply, so
        the server reads the working copy as CLEAN and the "no local edits"
        row would fire and wipe the column with no prompt. The client reports
        it; the server treats it as dirt."""
        app, c, live = _mk(tmp_path)
        c.post("/auto-sync/set", data={"pull": "1"})       # replace OFF
        _diverge(app, live, 6.7e9)
        assert c.post("/auto-sync/pull?dom_dirty=1").status_code == 204
        assert _f01(app) == 6.0e9, "the typed column was not pulled over"
        # ...and with replace ticked the user has consented, so it proceeds
        c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "1"})
        assert c.post("/auto-sync/pull?dom_dirty=1").status_code == 200

    def test_a_declined_pull_stops_being_advertised(self, tmp_path):
        """live_diverged never clears on its own, so the client used to POST a
        204-ing pull every 5 s forever — and each took window._applyInFlight,
        which also gates the manual Apply buttons, making them dead clicks."""
        app, c, live = _mk(tmp_path)
        c.post("/auto-sync/set", data={"pull": "1"})       # replace OFF
        c.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "5.5e9"})
        _diverge(app, live, 6.8e9)
        assert c.get("/state/drift").get_json().get("auto_pull") is False

    def test_changing_a_switch_keeps_the_revert_anchor(self, tmp_path):
        """docs/117 anchors "Revert last apply" to the session. Rebuilding it
        wholesale turned "undo this session" into "undo the next edit", with
        nothing on screen saying so."""
        app, c, _ = _mk(tmp_path)
        c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "1", "push": "1"})
        sess = _ctx(app)["auto_apply"]
        sess["pre_ts"] = "20260101_000000_0000"
        sess["flushes"] = 7
        c.post("/auto-sync/set", data={"pull": "1", "push": "1"})   # untick replace
        after = _ctx(app)["auto_apply"]
        assert after["pre_ts"] == "20260101_000000_0000"
        assert after["flushes"] == 7
        assert after["pull_replace"] is False, "the switch itself did change"

    def test_a_drifted_chip_arms_what_it_can_instead_of_nothing(self, tmp_path):
        """The popup opens all-checked, and a drifted chip is exactly when a
        user reaches for Auto-Sync — refusing the whole submission armed
        NOTHING and cost four extra interactions."""
        app, c, live = _mk(tmp_path)
        _diverge(app, live, 6.1e9)
        r = c.post("/auto-sync/set",
                   data={"pull": "1", "pull_replace": "1", "push": "1"})
        assert r.status_code == 200
        sess = _ctx(app)["auto_apply"]
        assert sess["pull"] is True and sess["pull_replace"] is True
        assert sess["push"] is False, "push is withheld, not the whole session"
        assert "Auto-push is off" in (r.headers.get("HX-Trigger") or ""), \
            "and the user is told which half was withheld"


class TestTheRaceInsideTheWindow:
    """The re-check the comment promised, actually placed after the I/O.

    The first cut re-checked inside the build lock but BEFORE
    ``sync_from_live`` — while the window its own comment describes ("tens of
    ms locally, seconds on a share") is spent INSIDE that call, which holds no
    ``store._lock``. So ``/field/edit`` could still land mid-pull, return 200,
    appear in the Review tray, and then be destroyed by the ``store.reload()``
    that follows. Found by the audit, reproduced with a barrier.
    """

    def test_an_edit_landing_mid_pull_survives(self, tmp_path, monkeypatch):
        import threading
        from quam_state_manager.core import working_copy as WC

        app, c, live = _mk(tmp_path)
        c.post("/auto-sync/set", data={"pull": "1"})     # replace OFF
        _diverge(app, live, 7.7e9)

        entered = threading.Event()
        release = threading.Event()
        real = WC.sync_from_live

        def slow(wc, *a, **k):
            entered.set()
            release.wait(5)
            return real(wc, *a, **k)

        monkeypatch.setattr(WC, "sync_from_live", slow)

        out = {}

        def puller():
            out["pull"] = c.post("/auto-sync/pull").status_code

        t = threading.Thread(target=puller)
        t.start()
        assert entered.wait(5), "the pull never reached sync_from_live"
        # The edit lands INSIDE the window. It takes only store._lock.
        r = c.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "3.3e9"})
        assert r.status_code == 200, r.get_data(as_text=True)
        release.set()
        t.join(10)

        # The edit is what the user is looking at, so the edit wins and the
        # banner asks. Live is untouched either way.
        assert _f01(app) == 3.3e9, "the edit was destroyed by the pull"
        assert _ctx(app)["store"].change_log, "the change log was cleared"
        # C29: disk must match memory, or an LRU evict + rehydrate would read
        # the pulled working files, compute working_dirty=False and drop it.
        wf = Path(_ctx(app)["working_copy"].working_folder)
        on_disk = json.loads((wf / "state.json").read_text(encoding="utf-8"))
        assert on_disk["qubits"]["q1"]["f_01"] == 3.3e9

    def test_a_replace_pull_still_wins_in_the_same_window(self, tmp_path, monkeypatch):
        """Ticking replace IS the consent — the re-check must not quietly turn
        it into a refusal."""
        import threading
        from quam_state_manager.core import working_copy as WC

        app, c, live = _mk(tmp_path)
        c.post("/auto-sync/set", data={"pull": "1", "pull_replace": "1"})
        _diverge(app, live, 7.7e9)

        entered, release = threading.Event(), threading.Event()
        real = WC.sync_from_live

        def slow(wc, *a, **k):
            entered.set()
            release.wait(5)
            return real(wc, *a, **k)

        monkeypatch.setattr(WC, "sync_from_live", slow)
        t = threading.Thread(target=lambda: c.post("/auto-sync/pull"))
        t.start()
        assert entered.wait(5)
        c.post("/field/edit", data={"dot_path": "qubits.q1.f_01", "value": "3.3e9"})
        release.set()
        t.join(10)
        assert _f01(app) == 7.7e9, "live must win when replace is ticked"


class TestAFailedPullLeavesNothingStale:
    """A failed pull used to leave the OLD store ACTIVE while the working
    folder and sync point had already advanced — an absorbing stale state where
    the next save+apply wrote the old content back over the chip, UNFORCED
    (the staleness gate sees nothing wrong once the sync point matches live).

    Evicting from ``_quam_cache`` did not fix it: ``app.config["contexts"]``
    holds the SAME dict, and that is what ``_active_ctx()`` reads.
    """

    def test_disk_matches_memory_after_a_failed_pull(self, tmp_path, monkeypatch):
        from quam_state_manager.web import routes as R

        app, c, live = _mk(tmp_path)
        c.post("/auto-sync/set", data={"pull": "1"})
        _diverge(app, live, 7.7e9)

        monkeypatch.setattr(R, "_rebuild_after_working_copy_replaced",
                            lambda ctx: (_ for _ in ()).throw(OSError("boom")))
        assert c.post("/auto-sync/pull").status_code == 204

        ctx = _ctx(app)
        wf = Path(ctx["working_copy"].working_folder)
        on_disk = json.loads((wf / "state.json").read_text(encoding="utf-8"))
        assert on_disk["qubits"]["q1"]["f_01"] == _f01(app), (
            "the working folder and the in-memory store disagree — a later "
            "save would write the stale side back over the chip")

    def test_the_session_disarms_on_failure(self, tmp_path, monkeypatch):
        from quam_state_manager.web import routes as R

        app, c, live = _mk(tmp_path)
        c.post("/auto-sync/set", data={"pull": "1"})
        _diverge(app, live, 7.7e9)
        monkeypatch.setattr(R, "_rebuild_after_working_copy_replaced",
                            lambda ctx: (_ for _ in ()).throw(OSError("boom")))
        c.post("/auto-sync/pull")
        sess = _ctx(app).get("auto_sync") or {}
        assert not sess.get("pull"), "a failing pull must not retry every poll"


class TestTheButtonActuallyPosts:
    """The whole feature was unreachable from the UI, and no test saw it.

    Found by driving real Chrome: clicking **Save** in the Auto-Sync popup
    produced ZERO network requests. Auto-Sync could not be armed — and, once
    armed by any other means, auto-push to the LIVE chip could not be turned
    off. Every server-side pin in this file passed the whole time, because they
    POST to /auto-sync/set directly and never press the button that is supposed
    to.

    Mechanism, and it bit twice in opposite directions:
      1. `onsubmit="AutoSync.close()"` ran BEFORE htmx and does
         `host.innerHTML = ''` — the form deleted ITSELF out of the DOM in the
         same tick, so htmx never issued anything, and the docs/75 document-level
         submit armor cancelled the native fallback too. Inert button.
      2. Moving the close to `hx-on::after-request` on the form did not fire
         either: both buttons target `#pending-tray`, an ANCESTOR of the form,
         so the swap destroys the element the handler is attached to.
    The close therefore lives on a DOCUMENT listener, which survives both.
    """

    def _panel(self):
        return Path("quam_state_manager/web/templates/_auto_sync_panel.html").read_text(
            encoding="utf-8")

    def test_no_handler_destroys_the_form_before_htmx_can_post(self):
        p = self._panel()
        assert "onsubmit=" not in p, (
            "an inline onsubmit that closes the popup deletes the form before "
            "htmx issues the request")
        # the disarm button had the same shape
        assert 'onclick="AutoSync.close()"' not in p

    def test_the_form_still_posts_to_the_route(self):
        p = self._panel()
        assert 'hx-post="/auto-sync/set"' in p
        assert 'hx-post="/auto-apply/disarm"' in p

    def test_the_popup_closes_from_the_document_not_from_itself(self):
        """Anything hung on the form dies with the swap that replaces its
        ancestor, so the listener cannot live there."""
        js = Path("quam_state_manager/web/static/auto-apply.js").read_text(encoding="utf-8")
        assert "document.addEventListener('htmx:afterRequest'" in js
        i = js.index("document.addEventListener('htmx:afterRequest'")
        block = js[i:i + 500]
        # the source carries an escaped regex literal, so match on the route
        # names rather than on a slash spelling
        assert "auto-sync" in block and "set" in block
        assert "auto-apply" in block and "disarm" in block
        assert "close()" in block

    def test_the_panel_offers_turn_off_only_when_armed(self, tmp_path):
        """The disarm button is the only way back out; if it is not rendered
        while a session is live, an armed auto-push cannot be stopped."""
        app, c, live = _mk(tmp_path)
        off = c.get("/auto-sync/panel").get_data(as_text=True)
        assert "/auto-apply/disarm" not in off
        c.post("/auto-sync/set", data={"pull": "1", "push": "1"})
        on = c.get("/auto-sync/panel").get_data(as_text=True)
        assert "/auto-apply/disarm" in on
