"""docs/179 — the unseen-edit gate was off on the button people actually press.

Found by the write-path stress round, driving two real browser tabs against a
writable copy of the PJ 20Q chip. The lane's own summary: *"two of the three UI
buttons that POST /state/apply-to-live do not declare seen_changes, so the
docs/120 unseen-edit gate is off on the tray's own '↑ Apply to live chip' — a
second window's staged edit went onto the live chip under a press whose screen
said '0 unsaved changes'."*

The gate treats an **absent** parameter as "no opinion" — deliberately, so that
no caller which has not opted in can be refused (docs/120). The consequence is
that a door which never opts in has no gate at all, and nothing was checking
which doors opted in.

The second half is subtler and the lane confirmed it separately: the gate
compares **counts**, so a stale tray whose number happens to equal the server's
current count passes.

    A stages alpha     log = 1; A's tray shows 1 — that tray is A's consent record
    B applies          alpha written, log = 0; A's tray does not refresh
    B stages beta      log = 1
    A presses Apply    declares the 1 it still shows → have(1) <= seen(1) → 200

The count was never what the presser saw; the **paths** were. So the tray
publishes a signature of the change set it rendered, every door declares it, and
the gate refuses on a different SET. The property docs/120 chose counting to get
— *no refusal when the change set is identical, because noise on this one button
is worse than the bug* — is preserved exactly: an identical set hashes
identically.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}
_ROOT = Path(__file__).resolve().parent.parent


def _state():
    return {
        "qubits": {"q1": {"id": "q1", "f_01": 5.0e9, "T1": 2.0e-5},
                   "q3": {"id": "q3", "f_01": 5.3e9, "T1": 2.3e-5}},
        "qubit_pairs": {},
        "active_qubit_names": ["q1", "q3"],
    }


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    live.mkdir(parents=True, exist_ok=True)
    (live / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": c, "live": live}


def _ctx(env):
    with env["app"].app_context():
        return routes_mod._active_ctx()


def _live(env) -> dict:
    return json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))


def _stage(env, path, value):
    r = env["client"].post("/field/edit", data={"dot_path": path, "value": value})
    assert r.status_code == 200, r.data[:200]


def _tray_attr(html: str, name: str) -> str:
    m = re.search(name + r'="([^"]*)"', html)
    return m.group(1) if m else ""


class TestEveryDoorDeclaresWhatItShowed:
    """A door that declares nothing has no gate. This is the check nobody had."""

    def test_the_trays_own_apply_button_declares_it(self, env):
        c = env["client"]
        _stage(env, "qubits.q1.f_01", "5.1e9")
        assert c.post("/save").status_code in (200, 204)   # → working_dirty branch
        html = c.get("/state/tray").data.decode()
        assert "Apply to live chip" in html, "the working_dirty button did not render"
        i = html.index("Apply to live chip")
        btn = html[max(0, i - 1400):i]
        assert "seen_changes" in btn, \
            "the button people actually press declares nothing, so the gate is off on it"
        assert "seen_sig" in btn

    def test_the_conflict_force_buttons_declare_it(self, env):
        """`force=1` answers the STALENESS question. It has never meant "and
        another window's edits too" — one token never collapses two gates."""
        from flask import render_template
        with env["app"].test_request_context():
            html = render_template("_state_apply_conflict.html",
                                   change_count=2, change_sig="abc123",
                                   staged_conflict=False)
        n = html.count("apply-to-live?force=1")
        assert n >= 1
        assert html.count("seen_sig") == n, \
            "a force button that declares nothing is an ungated door"
        assert "abc123" in html

    def test_the_review_modal_declares_the_set_not_only_the_count(self, env):
        # Through the REAL route. The template's actions only render with
        # `total > 0` and a working_dirty chip, and hand-building that context
        # meant guessing at a contract — an assertion against an empty review
        # would pass for the wrong reason.
        c = env["client"]
        _stage(env, "qubits.q1.f_01", "5.1e9")
        assert c.post("/save").status_code in (200, 204)
        html = c.get("/state/review").data.decode()
        assert "Apply to live chip" in html, "the button did not render at all"
        i = html.index("Apply to live chip")
        btn = html[max(0, i - 1200):i]
        assert "seen_sig" in btn and "seen_changes" in btn
        with env["app"].app_context():
            sig = routes_mod._change_log_sig(_ctx(env)["store"])
        assert sig in btn, "the modal declared a signature that is not its own"

    def test_the_tray_publishes_the_signature_for_doStateSync(self, env):
        c = env["client"]
        _stage(env, "qubits.q1.f_01", "5.1e9")
        html = c.get("/state/tray").data.decode()
        sig = _tray_attr(html, "data-change-sig")
        assert sig, "an empty signature reads as 'no opinion' and switches the gate off"
        assert len(sig) >= 8

    def test_doStateSync_sends_it(self):
        js = (_ROOT / "quam_state_manager" / "web" / "static" / "app.js").read_text(
            encoding="utf-8")
        i = js.index('body: "mode=" + encodeURIComponent(mode)')
        blk = js[i:i + 600]
        assert "seen_sig=" in blk
        assert "_seenSig" in blk
        # and it reads the attribute the tray actually publishes
        assert 'data-change-sig' in js


class TestTheSetIsWhatCounts:
    """The equal-count / different-content hole, closed."""

    def _sig(self, env):
        with env["app"].app_context():
            return routes_mod._change_log_sig(_ctx(env)["store"])

    def test_the_same_set_is_never_a_refusal(self, env):
        """The property docs/120 chose counting to get: noise on this button is
        worse than the bug it prevents."""
        _stage(env, "qubits.q1.f_01", "5.1e9")
        sig = self._sig(env)
        with env["app"].test_request_context(
                "/x", method="POST", data={"seen_changes": "1", "seen_sig": sig}):
            assert routes_mod._unseen_edit_refusal(_ctx(env)) is None

    def test_a_different_set_of_the_SAME_size_is_refused(self, env):
        """The lane's sequence B: A's tray shows one edit, B applies it and
        stages a different one, and A's press still declares the 1 it shows."""
        _stage(env, "qubits.q1.f_01", "5.1e9")
        stale_sig = self._sig(env)          # what window A is showing

        # window B applies alpha, then stages beta
        assert env["client"].post("/state/apply-to-live").status_code == 200
        _stage(env, "qubits.q3.f_01", "5.4e9")
        assert len(_ctx(env)["store"].change_log) == 1, "the counts must match to test this"

        with env["app"].test_request_context(
                "/x", method="POST",
                data={"seen_changes": "1", "seen_sig": stale_sig}):
            ref = routes_mod._unseen_edit_refusal(_ctx(env))
        assert ref is not None, "an equal count waved a different window's edit through"
        assert ref["status"] == "unseen_changes"
        assert "qubits.q3.f_01" in ref["paths"]

    def test_a_count_only_caller_keeps_the_old_rule(self, env):
        """Back-compatible: a door that declares only the count is judged the
        way it always was, never refused for lacking a signature."""
        _stage(env, "qubits.q1.f_01", "5.1e9")
        with env["app"].test_request_context(
                "/x", method="POST", data={"seen_changes": "1"}):
            assert routes_mod._unseen_edit_refusal(_ctx(env)) is None
        with env["app"].test_request_context(
                "/x", method="POST", data={"seen_changes": "0"}):
            ref = routes_mod._unseen_edit_refusal(_ctx(env))
        assert ref is not None and ref["have"] == 1

    def test_declaring_nothing_is_still_no_opinion(self, env):
        """docs/120's rule, kept: an absent parameter cannot be refused, so no
        un-migrated caller starts 409ing."""
        _stage(env, "qubits.q1.f_01", "5.1e9")
        with env["app"].test_request_context("/x", method="POST"):
            assert routes_mod._unseen_edit_refusal(_ctx(env)) is None

    def test_ack_unseen_still_accepts_it(self, env):
        """The refusal is never a dead end — the user is asked THIS question and
        can say yes."""
        _stage(env, "qubits.q1.f_01", "5.1e9")
        with env["app"].test_request_context(
                "/x", method="POST",
                data={"seen_changes": "0", "seen_sig": "notasig", "ack_unseen": "1"}):
            assert routes_mod._unseen_edit_refusal(_ctx(env)) is None

    def test_force_does_not_wave_it_through(self, env):
        """One token never collapses two gates (docs/41). `force=1` is about
        staleness, answered against a different screen."""
        _stage(env, "qubits.q1.f_01", "5.1e9")
        with env["app"].test_request_context(
                "/x?force=1", method="POST",
                data={"seen_changes": "0", "seen_sig": "notasig"}):
            assert routes_mod._unseen_edit_refusal(_ctx(env)) is not None

    def test_an_empty_log_signs_consistently(self, env):
        """A clean chip must not produce a mismatch against its own empty tray."""
        html = env["client"].get("/state/tray").data.decode()
        sig = _tray_attr(html, "data-change-sig")
        with env["app"].test_request_context(
                "/x", method="POST", data={"seen_changes": "0", "seen_sig": sig}):
            assert routes_mod._unseen_edit_refusal(_ctx(env)) is None


class TestItActuallyStopsTheWrite:
    """End to end through the real door, against the real file."""

    def test_a_stale_screen_cannot_write_another_windows_edit(self, env):
        c = env["client"]
        _stage(env, "qubits.q1.f_01", "5.1e9")
        with env["app"].app_context():
            stale_sig = routes_mod._change_log_sig(_ctx(env)["store"])

        assert c.post("/state/apply-to-live").status_code == 200
        assert _live(env)["qubits"]["q1"]["f_01"] == 5.1e9

        _stage(env, "qubits.q3.f_01", "5.4e9")
        before = _live(env)["qubits"]["q3"]["f_01"]

        r = c.post("/state/apply-to-live",
                   data={"seen_changes": "1", "seen_sig": stale_sig})
        assert r.status_code == 409, r.data[:300]
        assert r.get_json()["status"] == "unseen_changes"
        assert _live(env)["qubits"]["q3"]["f_01"] == before, \
            "the refused press wrote anyway"

        # …and the same press, made against the screen that IS current, lands.
        with env["app"].app_context():
            fresh = routes_mod._change_log_sig(_ctx(env)["store"])
        assert c.post("/state/apply-to-live",
                      data={"seen_changes": "1", "seen_sig": fresh}).status_code == 200
        assert _live(env)["qubits"]["q3"]["f_01"] == 5.4e9
