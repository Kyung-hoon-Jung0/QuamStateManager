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
        chunk = src[i - 900:i + 400]
        assert "_applyInFlight" in chunk

    def test_no_new_poller_was_added(self):
        src = (Path(__file__).resolve().parent.parent / "quam_state_manager"
               / "web" / "static" / "app.js").read_text(encoding="utf-8")
        # the pull is issued from inside the existing drift poll's handler
        i = src.index("/auto-sync/pull")
        assert "/state/drift" in src[max(0, i - 2000):i]


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
        """The user's call: all checked, uncheck what you don't want."""
        app, c, _ = _mk(tmp_path)
        body = c.get("/state/tray").get_data(as_text=True)
        for cid in ("as-pull", "as-pull-replace", "as-push"):
            i = body.index(f'id="{cid}"')
            assert "checked" in body[i:i + 200], cid
